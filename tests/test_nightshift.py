"""Meterless Nightshift as the primary generator (investigator/generator.py; hackathon rule: Nightshift is the
primary AI provider).

Nightshift is not needed: every test talks to an in-process fake of the Meterless Runtime provider contract
(FakeRuntime below), passed as the urlopen-compatible transport, so no socket is opened and no real token exists.
The tests pin the contract the adapter implements:
- a pasted URL (with or without scheme, /v1, /v1/chat/completions, /v1/models, /healthz) normalises to <host>/v1,
  never /v1/v1;
- GET /v1/models decides the status: 401/403 unauthorized, 200 with meterless/* ids online (unknown meterless ids
  kept), anything else offline; Auto Detect probes ports 8088, 8090, 8787, 11434 on 127.0.0.1, localhost and [::1]
  and keeps the first online or unauthorized hit in that order;
- chat requests carry the Bearer token, `meterless-app: x402-adoption-investigator` and `meterless-scope: user`,
  ask for the routing mode meterless/auto and use a 90 s timeout;
- provider precedence in live mode: Nightshift > OpenAI-compatible env > rules/templates, with
  recovery{nightshift_unavailable} naming the reason; sample mode never runs detection;
- every answered call is a `generate` event in run.jsonl (provider, requested routing mode, model that answered)
  whose seq generator.jsonl repeats, and the token appears in no log, transcript or output;
- the token is found in this order: NIGHTSHIFT_TOKEN, METERLESS_RUNTIME_TOKEN, METERLESS_TOKEN, NIGHTSHIFT_TOKEN_FILE,
  <stateDir>/proxy-token (stateDir = METERLESS_STATE_DIR or the ns CLI's OS default); METERLESS_BASE_URL and
  METERLESS_MODEL are aliases of NIGHTSHIFT_BASE_URL and NIGHTSHIFT_MODEL (the NIGHTSHIFT_* name wins);
- the `meterless` compute receipt of an answer (shape of Nightshift 1.2.3, fake_receipt below) becomes `routing` on
  the generate event, generator.jsonl, the transcript and output.agents[].generator_calls, without its prompt preview;
- the thinking switch NIGHTSHIFT_REASONING_EFFORT / METERLESS_REASONING_EFFORT (ThinkingSwitch): by default every
  Nightshift chat body carries `reasoning_effort: "none"` and a `/no_think` system turn before the unchanged prompt,
  `default` sends neither, an effort level sends only `reasoning_effort`, an unknown value sends nothing and says so,
  the OpenAI fallback never gets them, and each call logs them (generator.jsonl `call_options`, run_start
  generator_mount, the transcript `request`);
- the reply shapes the 3B lane produced on 2026-10-24 (SmallModelReplies: a Planner JSON over the schema's list
  limits, a Critic array cut off at the token limit, a Writer draft without caveat codes or conflict ids) are
  repaired or completed visibly (recovery generator_reply_repaired / narrative_completed), never approved: the
  verdict and the accepted claims stay those of the rules.
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

from investigator import config as config_mod  # noqa: E402
from investigator import generator as gen  # noqa: E402
from investigator import runner  # noqa: E402
from investigator.agents import planner as planner_mod  # noqa: E402
from investigator.config import Env, RunConfig, build_config, meterless_state_dir, parse_request, read_env  # noqa: E402
from investigator.errors import InvestigatorError  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402
from test_generator_hmem import FakeChatCompletions, user_prompt  # noqa: E402

TOKEN = "nightshift-test-token-not-a-secret"
OPENAI_KEY = "openai-test-key-not-a-secret"
BACKEND_MODEL = "fake-local-backend-q4"          # what the fake runtime says answered behind meterless/auto
MODELS = ["meterless/auto", "meterless/fast", "meterless/deep", "meterless/cheap", "meterless/local",
          "meterless/fusion", "meterless/vision.deep", "meterless/some-future-class"]


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(b'{"error":"nope"}'))


ROUTE_LABEL = "Fake 3B Q8 (Runtime GGUF)"


def fake_receipt(n: int, prompt: str, model: str = BACKEND_MODEL) -> dict[str, Any]:
    """A `meterless` object in the shape Nightshift 1.2.3 returned on 2026-10-24 (meterless.compute_receipt.v1),
    including the fields that must stay out of the logs (receipt.prompt_preview, cost, host telemetry)."""
    return {
        "schema": "meterless.compute_receipt.v1", "receipt_id": f"rcpt_req_fake{n:04d}", "decision_id": f"req_fake{n:04d}",
        "requested_model": "meterless/auto", "provider_model": "meterless/auto", "routed_model": model,
        "winning_route": {"model": model, "label": ROUTE_LABEL, "provider": "local-runtime", "kind": "local", "tier": "A"},
        "requires_review": False,
        "phases": [{"phase": "tool_call_format", "model": model, "ms": 234, "tokensIn": 72, "tokensOut": 6}],
        "usage": {"input_tokens": 72, "output_tokens": 6, "total_tokens": 78, "output_tps": 25.6, "latency_ms": 234},
        "cost": {"actual_usd": 0, "naive_usd": 0.000306}, "cache": {"status": "miss", "losers_reclaimed": 0},
        "privacy": {"path": "local-only"}, "from_cache": False,
        "receipt": {"prompt_preview": prompt[:200], "host_pressure": {"memory": {"score": 0.51}}},
        "receipt_envelope": {"signer": "meterlessd", "sig_b64": "c2lnbmF0dXJl"},
    }


class FakeRuntime(FakeChatCompletions):
    """In-process Meterless Runtime: `routes` maps a base URL (<host>/v1) to what GET /models does there
    ("online" -> 200 with MODELS, an int -> that HTTP status, "not_meterless" -> 200 with non-meterless ids,
    "garbage" -> 200 that is not JSON); any other base is a refused connection. POST /chat/completions on an
    online base answers like FakeChatCompletions (Planner, Writer and Critic replies) with model BACKEND_MODEL.
    Every request is recorded with its headers (lowercased names) and timeout."""

    def __init__(self, routes: Optional[dict[str, Any]] = None, token: Optional[str] = TOKEN,
                 receipts: bool = False) -> None:
        super().__init__()
        self.routes = dict(routes or {})
        self.token = token
        self.receipts = receipts            # answer with a `meterless` compute receipt, like Nightshift 1.2.3
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
        if self.receipts:
            prompt = user_prompt(json.loads(request.data.decode("utf-8")))
            payload["meterless"] = fake_receipt(len(self.chats()), prompt)
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

    def test_token_variables_are_read_in_order_and_named_not_shown(self) -> None:
        order = [("NIGHTSHIFT_TOKEN", "t-nightshift"), ("METERLESS_RUNTIME_TOKEN", "t-runtime"), ("METERLESS_TOKEN", "t-cli")]
        for skip in range(len(order)):
            environ = dict(order[skip:])
            with self.subTest(first=order[skip][0]):
                env = read_env(environ)
                self.assertEqual(gen.resolve_nightshift_token(env), (order[skip][1], order[skip][0]))
                self.assertNotIn(order[skip][1], repr(env), "token fields are repr=False")
        blank = read_env({"NIGHTSHIFT_TOKEN": "  ", "METERLESS_RUNTIME_TOKEN": "t-runtime"})
        self.assertEqual(gen.resolve_nightshift_token(blank), ("t-runtime", "METERLESS_RUNTIME_TOKEN"), "blank is unset")

    def test_full_order_variables_then_token_file_then_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            state.mkdir()
            (state / "proxy-token").write_text("from-state-dir\n", encoding="utf-8")
            token_file = Path(tmp) / "token.txt"
            token_file.write_text("from-token-file", encoding="utf-8")
            everything = {"METERLESS_TOKEN": "from-env", "NIGHTSHIFT_TOKEN_FILE": str(token_file),
                          "METERLESS_STATE_DIR": str(state)}
            self.assertEqual(gen.resolve_nightshift_token(read_env(everything)), ("from-env", "METERLESS_TOKEN"))
            everything.pop("METERLESS_TOKEN")
            self.assertEqual(gen.resolve_nightshift_token(read_env(everything)), ("from-token-file", "NIGHTSHIFT_TOKEN_FILE"))
            everything.pop("NIGHTSHIFT_TOKEN_FILE")
            token, source = gen.resolve_nightshift_token(read_env(everything))
            self.assertEqual((token, source), ("from-state-dir", "<stateDir>/proxy-token (METERLESS_STATE_DIR)"))
            (state / "proxy-token").unlink()
            token, why = gen.resolve_nightshift_token(read_env(everything))
            self.assertIsNone(token)
            self.assertIn("no token configured", why)
            self.assertIn("no proxy-token in METERLESS_STATE_DIR", why)
            self.assertNotIn(tmp, why, "the reason names variables, never paths")

    def test_the_os_default_state_dir_is_the_ns_cli_rule(self) -> None:
        win = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local", "USERPROFILE": r"C:\Users\u", "HOME": "/home/u"}
        self.assertEqual(meterless_state_dir(win, "win32"),
                         (str(Path(win["LOCALAPPDATA"]) / "ai.meterless.runtime"), "default"))
        self.assertEqual(meterless_state_dir({"USERPROFILE": r"C:\Users\u"}, "win32"),
                         (str(Path(r"C:\Users\u") / "AppData" / "Local" / "ai.meterless.runtime"), "default"))
        self.assertEqual(meterless_state_dir({}, "win32"), (None, ""), "no base variable: no default, no guess")
        self.assertEqual(meterless_state_dir({"HOME": "/Users/u"}, "darwin"),
                         (str(Path("/Users/u") / "Library" / "Application Support" / "ai.meterless.runtime"), "default"))
        self.assertEqual(meterless_state_dir({}, "darwin"), (None, ""))
        self.assertEqual(meterless_state_dir({"HOME": "/home/u"}, "linux"), ("/var/lib/meterless", "default"))
        for platform in ("win32", "darwin", "linux"):
            self.assertEqual(meterless_state_dir({**win, "METERLESS_STATE_DIR": "/srv/meterless"}, platform),
                             ("/srv/meterless", "METERLESS_STATE_DIR"))

    def test_the_default_state_dir_token_is_read_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "ai.meterless.runtime"
            state.mkdir()
            (state / "proxy-token").write_text(f"{TOKEN}\n", encoding="utf-8")
            with mock.patch.object(config_mod.sys, "platform", "win32"):
                env = read_env({"LOCALAPPDATA": tmp})
            self.assertEqual((env.nightshift_state_dir, env.nightshift_state_dir_var), (str(state), "default"))
            self.assertEqual(gen.resolve_nightshift_token(env), (TOKEN, "<stateDir>/proxy-token (default stateDir)"))

    def test_a_state_dir_token_alone_never_makes_auto_mode_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "proxy-token").write_text(TOKEN, encoding="utf-8")
            auto = type("Args", (), {"mode": "auto", "sample": False, "out": tmp})()
            config = build_config(auto, environ={"METERLESS_STATE_DIR": tmp, "LOCALAPPDATA": tmp}, load_dotenv=False)
        self.assertEqual(config.mode, "sample")


class ChatRequests(unittest.TestCase):
    BASE = "http://127.0.0.1:8088/v1"

    def test_chat_carries_the_headers_routing_mode_and_timeout(self) -> None:
        fake = FakeRuntime({self.BASE: "online"})
        nightshift = gen.NightshiftGenerator(TOKEN, "127.0.0.1:8088/healthz", opener=fake)
        self.assertEqual(nightshift.name, "nightshift:meterless/auto")
        self.assertEqual(nightshift.provider, "meterless")
        self.assertEqual(nightshift.timeout_s, 90.0)
        result = nightshift.complete("critic-r1", "You are the second-opinion Critic. Reply [] if fine.",
                                     max_tokens=64, temperature=0.0, timeout=nightshift.timeout_s)
        call = fake.chats()[0]
        self.assertEqual((call["method"], call["url"]), ("POST", f"{self.BASE}/chat/completions"))
        self.assertEqual(call["headers"]["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(call["headers"]["meterless-app"], "x402-adoption-investigator")
        self.assertEqual(call["headers"]["meterless-scope"], "user")
        self.assertEqual(call["timeout"], 90.0)
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

    def test_the_guides_meterless_names_are_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeRuntime({"http://127.0.0.1:9000/v1": "online"})
            config = live_config({"METERLESS_BASE_URL": "127.0.0.1:9000", "METERLESS_RUNTIME_TOKEN": TOKEN,
                                  "METERLESS_MODEL": "meterless/local"}, Path(tmp))
            mounted = gen.build_generator(config, opener=fake)
            self.assertIsInstance(mounted, gen.NightshiftGenerator)
            self.assertEqual(mounted.name, "nightshift:meterless/local")
            self.assertEqual(mounted.base_url, "http://127.0.0.1:9000/v1")
            self.assertEqual(mounted.mount_info["found_by"], "METERLESS_BASE_URL")
            self.assertEqual(mounted.mount_info["token_source"], "METERLESS_RUNTIME_TOKEN")
            self.assertEqual(fake.model_probes(), ["http://127.0.0.1:9000/v1/models"])
            both = live_config({"NIGHTSHIFT_BASE_URL": self.BASE, "METERLESS_BASE_URL": "127.0.0.1:9000",
                                "NIGHTSHIFT_TOKEN": TOKEN, "NIGHTSHIFT_MODEL": "meterless/deep",
                                "METERLESS_MODEL": "meterless/local"}, Path(tmp))
            mounted = gen.build_generator(both, opener=FakeRuntime({self.BASE: "online"}))
            self.assertEqual((mounted.base_url, mounted.name), (self.BASE, "nightshift:meterless/deep"), "NIGHTSHIFT_* wins")
            self.assertEqual(mounted.mount_info["found_by"], "NIGHTSHIFT_BASE_URL")
            offline = live_config({"METERLESS_BASE_URL": "127.0.0.1:9000", "OPENAI_API_KEY": OPENAI_KEY}, Path(tmp))
            fallback = gen.build_generator(offline, opener=FakeRuntime({"http://127.0.0.1:9000/v1": 503}))
        self.assertIn("(METERLESS_BASE_URL) is offline: HTTP 503", fallback.nightshift_unavailable["detail"])
        self.assertNotIn(TOKEN, json.dumps(fallback.nightshift_unavailable))

    def test_an_error_body_never_carries_the_token_into_the_exception(self) -> None:
        def echo_token(request: Any, timeout: Optional[float] = None) -> io.BytesIO:
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, io.BytesIO(f"bad token {TOKEN}".encode()))
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, opener=echo_token)
        with self.assertRaises(gen.GeneratorError) as caught:
            nightshift.complete("planner", "x", max_tokens=8, temperature=0.0, timeout=1.0)
        self.assertNotIn(TOKEN, str(caught.exception))


class ThinkingSwitch(unittest.TestCase):
    """NIGHTSHIFT_REASONING_EFFORT (alias METERLESS_REASONING_EFFORT): the thinking controls of each chat body."""

    BASE = "http://127.0.0.1:8088/v1"
    PROMPT = "You are the second-opinion Critic. Reply [] if fine."
    NO_THINK = {"reasoning_effort": "none", "system_turn": "/no_think"}

    def body_for(self, **kwargs: Any) -> dict[str, Any]:
        fake = FakeRuntime({self.BASE: "online"})
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, opener=fake, **kwargs)
        nightshift.complete("critic-r1", self.PROMPT, max_tokens=500, temperature=0.0, timeout=1.0)
        return fake.requests[0]["body"]

    def test_by_default_thinking_is_off_and_the_prompt_is_unchanged(self) -> None:
        body = self.body_for()
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["messages"], [{"role": "system", "content": "/no_think"},
                                            {"role": "user", "content": self.PROMPT}])
        self.assertEqual((body["max_tokens"], body["temperature"]), (500, 0.0))
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE)
        self.assertEqual(nightshift.call_options, self.NO_THINK)
        self.assertEqual(nightshift.mount_info["call_options"], self.NO_THINK)
        self.assertNotIn("call_options_note", nightshift.mount_info)

    def test_default_sends_nothing_and_an_effort_level_sends_only_the_effort(self) -> None:
        plain = self.body_for(reasoning_effort="default")
        self.assertNotIn("reasoning_effort", plain)
        self.assertEqual(plain["messages"], [{"role": "user", "content": self.PROMPT}])
        low = self.body_for(reasoning_effort="low")
        self.assertEqual(low["reasoning_effort"], "low")
        self.assertEqual(low["messages"], [{"role": "user", "content": self.PROMPT}])

    def test_an_unknown_value_is_never_sent_and_the_mount_says_so(self) -> None:
        body = self.body_for(reasoning_effort="think-hard")
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(len(body["messages"]), 1)
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, reasoning_effort="think-hard")
        self.assertEqual(nightshift.mount_info["call_options"], {})
        self.assertIn("treated as default, nothing sent", nightshift.mount_info["call_options_note"])

    def test_the_variables_set_it_and_the_nightshift_name_wins(self) -> None:
        cases = [({}, "none"), ({"METERLESS_REASONING_EFFORT": "  "}, "none"),
                 ({"METERLESS_REASONING_EFFORT": "Default"}, "default"),
                 ({"NIGHTSHIFT_REASONING_EFFORT": "high", "METERLESS_REASONING_EFFORT": "none"}, "high")]
        for environ, expected in cases:
            self.assertEqual(read_env(environ).nightshift_reasoning_effort, expected, environ)
        with tempfile.TemporaryDirectory() as tmp:
            config = live_config({"NIGHTSHIFT_BASE_URL": self.BASE, "NIGHTSHIFT_TOKEN": TOKEN,
                                  "METERLESS_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
                                  "METERLESS_REASONING_EFFORT": "medium"}, Path(tmp))
            mounted = gen.build_generator(config, opener=FakeRuntime({self.BASE: "online"}))
        self.assertEqual(mounted.name, "nightshift:nvidia/nemotron-3.5-lightning-30b-a3b")
        self.assertEqual(mounted.call_options, {"reasoning_effort": "medium"})

    def test_the_openai_fallback_never_gets_the_switch(self) -> None:
        fake = FakeChatCompletions()
        openai = gen.OpenAIGenerator(OPENAI_KEY, "https://api.example.test/v1", "gpt-4.1-mini", opener=fake)
        openai.complete("critic-r1", self.PROMPT, max_tokens=500, temperature=0.0, timeout=1.0)
        body = fake.requests[0]["body"]
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["messages"], [{"role": "user", "content": self.PROMPT}])
        self.assertEqual(openai.call_options, {})


class ReceiptParsing(unittest.TestCase):
    """The `meterless` compute receipt -> routing facts (generator.meterless_receipt), no prompt text kept."""

    BASE = "http://127.0.0.1:8088/v1"
    PROMPT = "You are the second-opinion Critic. Secret-looking prompt text that must not reach the receipt summary."

    def test_a_receipt_becomes_the_routing_facts_only(self) -> None:
        routing = gen.meterless_receipt({"meterless": fake_receipt(7, self.PROMPT)}, wall_ms=310)
        self.assertEqual(routing, {
            "source": "meterless_receipt", "schema": "meterless.compute_receipt.v1", "receipt_id": "rcpt_req_fake0007",
            "decision_id": "req_fake0007", "requested_model": "meterless/auto", "routed_model": BACKEND_MODEL,
            "winning_route": {"model": BACKEND_MODEL, "label": ROUTE_LABEL, "provider": "local-runtime", "kind": "local",
                              "tier": "A"},
            "phases": [{"phase": "tool_call_format", "model": BACKEND_MODEL, "ms": 234}], "latency_ms": 234,
            "usage": {"input_tokens": 72, "output_tokens": 6, "total_tokens": 78}, "cache": "miss", "from_cache": False,
            "privacy_path": "local-only", "wall_ms": 310})
        self.assertNotIn("Secret-looking", json.dumps(routing))

    def test_missing_or_malformed_receipts_degrade_to_what_is_there(self) -> None:
        for payload in ({}, {"meterless": None}, {"meterless": "rcpt"}, {"meterless": []}, [], "x"):
            with self.subTest(payload=payload):
                self.assertEqual(gen.meterless_receipt(payload, wall_ms=5), {})
        partial = gen.meterless_receipt({"meterless": {"receipt_id": "rcpt_1", "routed_model": "", "winning_route": "x",
                                                       "phases": [None, {"ms": True}, {"phase": "p", "ms": 1.6}],
                                                       "usage": {"latency_ms": "12", "input_tokens": 3.0,
                                                                 "output_tokens": False},
                                                       "cache": {"status": 3}, "from_cache": "no"}})
        self.assertEqual(partial, {"source": "meterless_receipt", "receipt_id": "rcpt_1",
                                   "phases": [{"phase": "p", "ms": 2}], "usage": {"input_tokens": 3}})
        long_label = gen.meterless_receipt({"meterless": {"winning_route": {"label": "L" * 500}}})
        self.assertEqual(len(long_label["winning_route"]["label"]), gen.RECEIPT_TEXT_MAX)

    def test_nightshift_answers_carry_routing_and_plain_openai_answers_do_not(self) -> None:
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, opener=FakeRuntime({self.BASE: "online"}, receipts=True))
        result = nightshift.complete("critic-r1", self.PROMPT, max_tokens=64, temperature=0.0, timeout=5.0)
        self.assertEqual(result.routing["receipt_id"], "rcpt_req_fake0001")
        self.assertEqual(result.routing["routed_model"], BACKEND_MODEL)
        self.assertEqual(result.routing["winning_route"]["kind"], "local")
        self.assertIsInstance(result.routing["wall_ms"], int)
        no_receipt = gen.NightshiftGenerator(TOKEN, self.BASE, opener=FakeRuntime({self.BASE: "online"}))
        self.assertEqual(no_receipt.complete("critic-r1", self.PROMPT, max_tokens=8, temperature=0.0, timeout=5.0).routing, {})
        openai = gen.OpenAIGenerator(OPENAI_KEY, self.BASE, "gpt-x", opener=FakeRuntime({self.BASE: "online"}, token=None,
                                                                                         receipts=True))
        self.assertEqual(openai.complete("critic-r1", self.PROMPT, max_tokens=8, temperature=0.0, timeout=5.0).routing, {},
                         "only the Nightshift adapter reads a meterless receipt")

    def test_a_reply_that_is_not_an_object_or_has_no_text_is_a_generator_error(self) -> None:
        for body in (b"[]", b'{"choices": ["x"]}', b'{"choices": [{"message": "x"}]}', b'{"choices": []}'):
            with self.subTest(body=body):
                nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, opener=lambda request, timeout=None, b=body: io.BytesIO(b))
                with self.assertRaises(gen.GeneratorError):
                    nightshift.complete("planner", "x", max_tokens=8, temperature=0.0, timeout=1.0)


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
            for name in ("NIGHTSHIFT_BASE_URL", "NIGHTSHIFT_TOKEN", "NIGHTSHIFT_TOKEN_FILE", "METERLESS_BASE_URL",
                         "METERLESS_RUNTIME_TOKEN", "METERLESS_TOKEN"):
                self.assertEqual(build_config(auto, environ={name: "x"}, load_dotenv=False).mode, "live", name)
            for name in ("NIGHTSHIFT_MODEL", "METERLESS_MODEL"):
                self.assertEqual(build_config(auto, environ={name: "meterless/deep"}, load_dotenv=False).mode,
                                 "sample", f"the routing mode alone ({name}) configures no provider")
            self.assertEqual(build_config(auto, environ={"METERLESS_STATE_DIR": str(self.dir)}, load_dotenv=False).mode,
                             "sample", "a stateDir alone configures no provider")
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
            # the sample mount: recorded when fixtures/transcripts/<example>/ holds transcripts, else null
            has_transcripts = any((REPO_ROOT / "fixtures" / "transcripts" / "01-seller-audit-textokit").glob("*.json"))
            expected = "recorded" if has_transcripts else "null"
            for args in (auto, forced):
                config = build_config(args, environ=environ, load_dotenv=False)
                self.assertEqual(config.mode, "sample")
                self.assertEqual(gen.build_generator(config, "01-seller-audit-textokit").name, expected)
            envelope = runner.run(payload, build_config(auto, environ=environ, load_dotenv=False), echo_stdout=False)
        self.assertEqual(envelope["status"], "ok", envelope.get("error"))
        self.assertEqual(envelope["output"]["mounts"]["generator"], expected)
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
            self.assertLessEqual(call["timeout"], 90.0)

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

    def test_every_request_carries_and_logs_the_thinking_switch(self) -> None:
        no_think = {"reasoning_effort": "none", "system_turn": "/no_think"}
        for request in self.fake.requests:
            self.assertEqual(request["body"]["reasoning_effort"], "none")
            self.assertEqual(request["body"]["messages"][0], {"role": "system", "content": "/no_think"})
            self.assertEqual(request["body"]["messages"][1]["role"], "user")
        self.assertTrue(self.calls)
        for line in self.calls:
            self.assertEqual(line["call_options"], no_think)
            transcript = json.loads((self.run_dir / line["transcript"]).read_text(encoding="utf-8"))
            self.assertEqual(transcript["request"], {"max_tokens": line["max_tokens"], "call_options": no_think})
            prompt = (self.run_dir / line["transcript"].replace(".json", ".prompt.txt")).read_text(encoding="utf-8")
            self.assertEqual(gen.prompt_sha256(prompt), line["prompt_sha256"], "the prompt file is the user turn sent")
        start = next(e for e in self.events if e["type"] == "run_start")
        self.assertEqual(start["payload"]["generator_mount"]["call_options"], no_think)

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


class ReceiptTrace(unittest.TestCase):
    """Example 03's pipeline with Nightshift answering with compute receipts: every generate event, generator.jsonl
    line, transcript and output.agents[].generator_calls item names the routed model and the receipt id, the
    receipt's prompt preview is in no log, and a replay repeats the routing with recorded: true."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online"}, receipts=True)
        cls.payload = json.loads((REPO_ROOT / "examples" / "03-payer-dossier-onchain" / "input.json").read_text(encoding="utf-8"))
        config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out / "live")})(), load_dotenv=False)
        mount = lambda *args, **kwargs: gen.NightshiftGenerator(TOKEN, "127.0.0.1:8088", opener=cls.fake,  # noqa: E731
                                                                 models=MODELS, found_by="auto_detect",
                                                                 token_source="<stateDir>/proxy-token (default stateDir)")
        with mock.patch.object(runner, "build_generator", mount):
            cls.envelope = runner.run(cls.payload, config, echo_stdout=False)
        cls.run_dir = out / "live" / cls.envelope["run_id"]
        cls.events = read_jsonl(cls.run_dir / "run.jsonl")
        cls.calls = read_jsonl(cls.run_dir / "generator.jsonl")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_generate_events_carry_the_receipt_routing(self) -> None:
        schema = load_schema("events")
        generated = [e for e in self.events if e["type"] == "generate"]
        self.assertTrue(generated)
        self.assertEqual(len(generated), len(self.fake.chats()))
        receipt_ids = []
        for event, line in zip(generated, self.calls):
            self.assertEqual(validate(event, schema), [], event)
            routing = event["payload"]["routing"]
            self.assertEqual((routing["routed_model"], routing["winning_route"]["label"], routing["winning_route"]["kind"],
                              routing["winning_route"]["provider"]), (BACKEND_MODEL, ROUTE_LABEL, "local", "local-runtime"))
            self.assertEqual((routing["latency_ms"], routing["usage"]["total_tokens"]), (234, 78))
            self.assertEqual(line["routing"], routing, "generator.jsonl repeats the routing")
            transcript = json.loads((self.run_dir / line["transcript"]).read_text(encoding="utf-8"))
            self.assertEqual(transcript["routing"], routing)
            receipt_ids.append(routing["receipt_id"])
        self.assertEqual(len(set(receipt_ids)), len(receipt_ids), "one receipt per request")
        start = next(e for e in self.events if e["type"] == "run_start")
        self.assertEqual(start["payload"]["generator_mount"]["token_source"], "<stateDir>/proxy-token (default stateDir)")

    def test_no_prompt_preview_or_token_reaches_any_log(self) -> None:
        marker = "You are the"            # every prompt starts with it; only .prompt.txt files may hold prompt text
        for path in self.run_dir.rglob("*"):
            if not path.is_file() or path.name.endswith(".prompt.txt"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("prompt_preview", text, path.name)
            self.assertNotIn(marker, text, path.name)
            self.assertNotIn("c2lnbmF0dXJl", text, path.name)
        assert_no_secret(self, self.run_dir, TOKEN)

    def test_output_and_report_say_which_model_handled_each_request(self) -> None:
        output = self.envelope["output"]
        self.assertEqual(validate(self.envelope, load_schema("output")), [])
        listed = [c for a in output["agents"] for c in a.get("generator_calls", [])]
        self.assertEqual(len(listed), len(self.calls))
        for item in listed:
            self.assertEqual((item["provider"], item["requested_model"], item["model"], item["route"], item["route_kind"],
                              item["recorded"]), ("meterless", "meterless/auto", BACKEND_MODEL, ROUTE_LABEL, "local", False))
            self.assertTrue(item["receipt_id"].startswith("rcpt_req_fake"))
        self.assertFalse([a for a in output["agents"] if not a["generator_backed"] and "generator_calls" in a])
        report_text = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Model calls", report_text)
        self.assertIn(f"| {BACKEND_MODEL} | {ROUTE_LABEL} (local) | rcpt_req_fake0001 | False |", report_text)

    def test_a_replay_repeats_the_routing_as_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            replay = lambda *args, **kwargs: gen.RecordedGenerator(self.run_dir / "transcripts")  # noqa: E731
            with mock.patch.object(runner, "build_generator", replay):
                envelope = runner.run(self.payload, config, echo_stdout=False)
            events = [e for e in read_jsonl(Path(tmp) / envelope["run_id"] / "run.jsonl") if e["type"] == "generate"]
        live = [e["payload"]["routing"] for e in self.events if e["type"] == "generate"]
        self.assertEqual([e["payload"]["routing"] for e in events], live)
        self.assertTrue(all(e["payload"]["recorded"] for e in events))
        listed = [c for a in envelope["output"]["agents"] for c in a.get("generator_calls", [])]
        self.assertTrue(listed and all(c["recorded"] for c in listed))


# What the 3B lane answered on 2026-10-24 (logs/live-ns-ex01-20261024, before the fixes), shortened: the Planner
# listed 6 hypotheses (some repeated) and 6 extra metrics, and the Critic's array stopped at its token limit.
PLANNER_OVER_LIMITS = json.dumps({
    "playbook": "seller_profile",
    "hypotheses": ["The seller is legitimate.", "The seller may be fake.", "Registries disagree on data collection.",
                   "The seller is legitimate.", "The seller may be fake.", "Most sales are self-settled.",
                   "External payers come back.", "The top payer dominates."],
    "extra_metrics": ["bazaar_listings_for_host", "not_a_metric", "endpoint_count_for_host", "endpoint_count_for_host",
                      "settlements_verified", "bazaar_calls_30d_for_host", "bazaar_payto_for_host", "sales_total"]})
CRITIC_TRUNCATED = ('[\n  {"sentence_index": 0, "reason": "not supported by claims", "claim_id": null},\n'
                    '  {"sentence_index": 1, "reason": "conflict with claim c03", "claim_id": "c03"},\n'
                    '  {"sentence_index": 2, "reason": "not supp')


class SmallModelReplies(unittest.TestCase):
    def test_a_truncated_array_keeps_only_its_complete_items(self) -> None:
        self.assertEqual(gen.salvage_json_list('[{"a": 1}, {"b": 2}]'), ([{"a": 1}, {"b": 2}], 0))
        self.assertEqual(gen.salvage_json_list("```json\n[]\n```"), ([], 0))
        items, salvaged = gen.salvage_json_list(CRITIC_TRUNCATED)
        self.assertEqual(salvaged, 2)
        self.assertEqual([i["sentence_index"] for i in items], [0, 1])
        self.assertEqual(gen.salvage_json_list('[{"sentence_index": 0, "rea'), (None, 0))
        self.assertEqual(gen.salvage_json_list("Every statement is supported."), (None, 0))

    def test_planner_reply_is_brought_inside_the_schema_limits_without_inventing(self) -> None:
        schema = load_schema("plan")
        response_schema = {**schema["$defs"]["generator_response"], "$defs": schema["$defs"]}
        raw = json.loads(PLANNER_OVER_LIMITS)
        self.assertTrue(validate(raw, response_schema), "the raw reply is over the limits")
        data, changes = planner_mod.normalise_reply(raw)
        self.assertEqual(validate(data, response_schema), [])
        self.assertEqual(data["playbook"], "seller_profile")
        self.assertEqual(data["hypotheses"], ["The seller is legitimate.", "The seller may be fake.",
                                              "Registries disagree on data collection.", "Most sales are self-settled.",
                                              "External payers come back."])
        self.assertEqual(data["extra_metrics"], ["bazaar_listings_for_host", "endpoint_count_for_host",
                                                 "settlements_verified", "bazaar_calls_30d_for_host",
                                                 "bazaar_payto_for_host"])
        self.assertTrue(set(data["hypotheses"]) <= set(raw["hypotheses"]), "only the model's own sentences")
        self.assertEqual(len(changes), 2)
        wrong = planner_mod.normalise_reply({**raw, "playbook": "approve_everything"})[0]
        self.assertTrue(validate(wrong, response_schema), "a wrong playbook still fails")
        extra_key = planner_mod.normalise_reply({**raw, "verdict": "adoption_real"})[0]
        self.assertTrue(validate(extra_key, response_schema), "a key the schema does not allow still fails")
        self.assertEqual(planner_mod.normalise_reply(None), (None, []))
        clean = {"playbook": "payer_dossier", "hypotheses": ["One purchase."], "extra_metrics": []}
        self.assertEqual(planner_mod.normalise_reply(clean), (clean, []))


class ForgetfulRuntime(FakeRuntime):
    """The 3B reply shapes: Planner over the limits, a Writer draft that cites claims but drops every caveat code
    and conflict id, a Critic array cut off mid-item."""

    def reply_for(self, prompt: str) -> str:
        if prompt.startswith("You are the Planner"):
            return PLANNER_OVER_LIMITS
        if prompt.startswith("You are the Writer"):
            claims = json.loads(prompt.split("conflict_ids):\n", 1)[1].split("\n\nConflicts", 1)[0])
            return "- A short summary.\n\n" + " ".join(f"{row['statement'].rstrip('.')} [{row['id']}]." for row in claims)
        if prompt.startswith("You are the second-opinion Critic"):
            return CRITIC_TRUNCATED
        raise AssertionError("unexpected prompt: " + prompt[:80])


class SmallModelPipeline(unittest.TestCase):
    """Example 01 (2 conflicts, 3 caveat codes) with ForgetfulRuntime behind Nightshift: the run repairs and
    completes visibly and the verdict and claims are those of the rules-only replay."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.payload = json.loads((REPO_ROOT / "examples" / "01-seller-audit-textokit" / "input.json").read_text(encoding="utf-8"))
        config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out / "model")})(), load_dotenv=False)
        fake = ForgetfulRuntime({"http://127.0.0.1:8088/v1": "online"}, receipts=True)
        mount = lambda *args, **kwargs: gen.NightshiftGenerator(TOKEN, "127.0.0.1:8088", opener=fake)  # noqa: E731
        with mock.patch.object(runner, "build_generator", mount):
            cls.envelope = runner.run(cls.payload, config, echo_stdout=False)
        rules = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out / "rules")})(), load_dotenv=False)
        with mock.patch.object(runner, "build_generator", lambda *args, **kwargs: gen.NullGenerator()):
            cls.rules = runner.run(cls.payload, rules, echo_stdout=False)
        cls.events = read_jsonl(out / "model" / cls.envelope["run_id"] / "run.jsonl")
        cls.recoveries = [e["payload"] for e in cls.events if e["type"] == "recovery"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_line_is_valid_and_the_repairs_are_logged(self) -> None:
        schema = load_schema("events")
        for line in self.events:
            self.assertEqual(validate(line, schema), [], line)
        repaired = [r for r in self.recoveries if r["reason"] == "generator_reply_repaired"]
        self.assertEqual([r["prompt_id"] for r in repaired], ["planner", "critic-r1", "critic-r2"])
        self.assertEqual([r["kept"] for r in repaired[1:]], [2, 2])
        self.assertNotIn("planner_fallback", [r["reason"] for r in self.recoveries])
        plan = self.envelope["output"]["plan"]
        self.assertEqual(plan["planner_source"], "generator")
        self.assertEqual(len(plan["hypotheses"]), 5)

    def test_the_final_answer_carries_every_caveat_and_conflict(self) -> None:
        output = self.envelope["output"]
        completed = [r for r in self.recoveries if r["reason"] == "narrative_completed"]
        self.assertEqual([sorted(r["added"]) for r in completed],
                         [["caveats", "conflicts", "disclaimer", "rejected", "sample_notice", "unavailable"], ["verdict"]])
        self.assertTrue(completed[0]["added"]["caveats"] and completed[0]["added"]["conflicts"])
        self.assertIn(f"(verdict {output['verdict']['label']})", output["answer"].split("[c", 1)[0], "verdict first")
        for code in sorted({code for c in output["claims"] for code in c["caveats"]}):
            self.assertIn(f"Caveat [{code}]:", output["answer"])
        for conflict in output["conflicts"]:
            self.assertIn(f"Where sources disagree [{conflict['id']}]", output["answer"])
        self.assertTrue(output["answer"].endswith("no network, no model calls."), "the sample notice stays last")

    def test_the_model_never_picks_the_verdict_or_the_claims(self) -> None:
        self.assertEqual(self.envelope["status"], self.rules["status"])
        self.assertEqual(self.envelope["output"]["verdict"], self.rules["output"]["verdict"])
        strip = lambda claims: [{k: v for k, v in c.items() if k not in ("critique",)} for c in claims]  # noqa: E731
        self.assertEqual([c["id"] for c in self.envelope["output"]["claims"]], [c["id"] for c in self.rules["output"]["claims"]])
        self.assertEqual([c["value"] for c in strip(self.envelope["output"]["claims"])],
                         [c["value"] for c in strip(self.rules["output"]["claims"])])
        self.assertEqual(self.envelope["output"]["rejected_claims"], self.rules["output"]["rejected_claims"])


if __name__ == "__main__":
    unittest.main()
