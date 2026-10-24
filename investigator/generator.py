"""Generator protocol implementations (SPEC.md section 3 and 7.4 rule 5). Owner: B4.

Exactly three roles are generator-backed (Planner, Writer, Critic second opinion); every other agent is tool-only.
- NightshiftGenerator: Meterless Nightshift, the PRIMARY provider in live mode. The Meterless Runtime (its provider
  contract, run against Nightshift 1.2.3 on 2026-10-24) is an OpenAI-compatible provider (id `meterless`, default
  http://127.0.0.1:8088/v1). Bearer token, first found: NIGHTSHIFT_TOKEN, METERLESS_RUNTIME_TOKEN, METERLESS_TOKEN;
  the file NIGHTSHIFT_TOKEN_FILE; <stateDir>/proxy-token, stateDir = METERLESS_STATE_DIR or the OS default the
  official ns CLI uses (config.meterless_state_dir). `meterless-app: x402-adoption-investigator` on every request
  and `meterless-scope: user` on chat requests, routing mode NIGHTSHIFT_MODEL / METERLESS_MODEL (default
  meterless/auto), 90 s timeout per request (both Nemotron routes answered in under 12 s on 2026-10-24). detect_nightshift() decides its status from GET
  /v1/models (401/403 unauthorized, 200 with meterless/* ids online, anything else offline) at NIGHTSHIFT_BASE_URL
  / METERLESS_BASE_URL, or by Auto Detect over ports 8088, 8090, 8787 and 11434 on 127.0.0.1, localhost and [::1].
  The `meterless` compute receipt of each answer (meterless.compute_receipt.v1) is reduced by meterless_receipt()
  to the routing facts (receipt id, requested and routed model, winning route, phases, latency, tokens, cache) that
  the generate event, generator.jsonl and the transcript carry; the receipt's prompt preview is never copied.
  The thinking switch NIGHTSHIFT_REASONING_EFFORT / METERLESS_REASONING_EFFORT (default `none`) becomes the
  request's call options (nightshift_call_options): `reasoning_effort: "none"` plus a `/no_think` system turn, so a
  reasoning model such as Nemotron answers inside the role's token budget instead of thinking it away.
- OpenAIGenerator: OpenAI-compatible chat completions over urllib, the fallback when Nightshift is not online.
  Injected by env: OPENAI_API_KEY + OPENAI_BASE_URL + MODEL. Never the Codex/ChatGPT subscription.
- RecordedGenerator: replays fixtures/transcripts/<example>/<prompt_id>-<prompt_sha12>.json (sample mode).
- NullGenerator: always returns None; callers fall back to rules/templates and log a recovery.
A generator never approves a claim and never picks the verdict label. build_generator() picks the mount: sample
mode -> recorded | null with no network call and no detection; live mode -> nightshift, else openai, else null, and
a live run that does not mount Nightshift logs recovery{nightshift_unavailable} with the probe's reason.

generate(ctx, ...) is the single entry point the agents use: it enforces budget.max_generator_calls, uses
temperature 0 and the generator's timeout (60 s, Nightshift 90 s, never closer than GENERATOR_RESERVE_S to the
run's deadline), saves every
transcript under logs/<run_id>/transcripts/ (and, with --record-transcripts, under fixtures/transcripts/<example>/),
logs one `generate` event in run.jsonl and the matching generator.jsonl side line (same seq) naming the provider,
the requested model (routing mode) and the model that answered, with the prompt and response sha256 (never the key
or token, never headers), and turns failures into recovery{generator_fallback} unless
--require-generator is set (then E_GENERATOR). When the prompt carries an H-MEM reinjection block, generate()
logs the injection before the call: a `memory` event {op: "inject", memory_ids, details: {prompt_id,
prompt_sha256, reinjection_sha256}} and the same line in hmem.trace.jsonl, so the retrieval trace (its
reinjection_sha256), the injection and the generator.jsonl line (its prompt_sha256) link up by hash.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from . import USER_AGENT
from .config import (NIGHTSHIFT_DEFAULT_MODEL, NIGHTSHIFT_DEFAULT_REASONING_EFFORT, NIGHTSHIFT_REASONING_EFFORTS, Env,
                     RunConfig)
from .contracts import Generator, GeneratorResult
from .errors import InvestigatorError

if TYPE_CHECKING:
    from .runner import RunContext

GENERATOR_TEMPERATURE = 0.0
GENERATOR_TIMEOUT_S = 60.0
MIN_SECONDS_LEFT_FOR_GENERATOR = 20
GENERATOR_RESERVE_S = 10          # a call's timeout ends this long before the deadline: the fallback and finalize still run

NIGHTSHIFT_PROVIDER_ID = "meterless"
NIGHTSHIFT_APP_ID = "x402-adoption-investigator"          # meterless-app header: stable lowercase app id
NIGHTSHIFT_DEFAULT_BASE_URL = "http://127.0.0.1:8088/v1"
# Chat calls. The receipts of 24 Oct show 1.3-11.5 s per answer on both Nemotron routes; 90 s is the top of the
# guides' 30-90 s range and lets a hung request take at most half of a 180 s run.
NIGHTSHIFT_TIMEOUT_S = 90.0
NIGHTSHIFT_PROBE_TIMEOUT_S = 3.0                           # one GET /v1/models during detection
NIGHTSHIFT_DETECT_PORTS: tuple[int, ...] = (8088, 8090, 8787, 11434)
NIGHTSHIFT_DETECT_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
NIGHTSHIFT_PLACEHOLDER_TOKEN = "rt-local"                  # rejected by the runtime: never sent
NO_THINK_SYSTEM_TURN = "/no_think"                         # the thinking switch the Nemotron Nano 9B v2 template reads
NightshiftStatus = Literal["online", "unauthorized", "offline"]


class GeneratorError(Exception):
    """A live generator call failed (network, HTTP status, timeout or an unparseable body)."""


def prompt_sha256(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def transcript_filename(prompt_id: str, prompt: str) -> str:
    return f"{prompt_id}-{prompt_sha256(prompt)[:12]}.json"


class NullGenerator:
    """No generator mounted: complete() always returns None."""

    name = "null"

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]:
        return None


class RecordedGenerator:
    """Replays recorded transcripts keyed by (prompt_id, sha256(prompt)); a miss returns None."""

    name = "recorded"

    def __init__(self, transcripts_dir: Path) -> None:
        self.transcripts_dir = Path(transcripts_dir)

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]:
        path = self.transcripts_dir / transcript_filename(prompt_id, prompt)
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        digest = prompt_sha256(prompt)
        if record.get("prompt_sha256") != digest:
            return None
        routing = record.get("routing")
        return GeneratorResult(text=str(record.get("response", "")), model=str(record.get("model", "recorded")),
                               recorded=True, prompt_sha256=digest, usage=dict(record.get("usage") or {}),
                               provider=str(record.get("provider") or "recorded"),
                               requested_model=record.get("requested_model"),
                               routing=dict(routing) if isinstance(routing, dict) else {})


class OpenAIGenerator:
    """Chat completions over urllib. temperature 0, timeout 60 s, prompt/response sha256 logged by the caller."""

    provider = "openai"
    timeout_s = GENERATOR_TIMEOUT_S

    def __init__(self, api_key: str, base_url: str, model: str, opener: Optional[Any] = None) -> None:
        self._api_key = api_key                  # never logged, never printed
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"openai:{model}"
        self.user_agent = USER_AGENT
        self._opener = opener                    # urlopen-compatible transport; None = urllib.request.urlopen
        self.call_options: dict[str, Any] = {}   # request fields beyond model/messages/temperature/max_tokens (logged)

    def _headers(self) -> dict[str, str]:
        """Headers of one chat request (the Bearer value is the key: never logged)."""
        return {"Content-Type": "application/json", "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}", "User-Agent": self.user_agent}

    def _scrub(self, text: str) -> str:
        """Remove the key from a server-supplied error text before it reaches a log line."""
        return text.replace(self._api_key, "<redacted>") if self._api_key else text

    def _post(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=data, method="POST", headers=self._headers())
        opener = self._opener or urllib.request.urlopen
        try:
            with opener(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - best effort only
                detail = ""
            raise GeneratorError(f"HTTP {exc.code} from {self.base_url}/chat/completions: {self._scrub(detail)}") from None
        except urllib.error.URLError as exc:
            raise GeneratorError(f"connection error: {self._scrub(str(exc.reason))}") from None
        except TimeoutError:
            raise GeneratorError(f"timeout after {timeout:.0f} s") from None
        except OSError as exc:
            raise GeneratorError(f"socket error: {exc}") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise GeneratorError(f"response is not JSON: {exc}") from None

    def _body(self, prompt: str, max_tokens: int, temperature: float) -> dict[str, Any]:
        """The chat-completions body: the prompt as the one user turn."""
        return {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature, "max_tokens": max_tokens}

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]:
        """POST {base_url}/chat/completions; returns the first choice's text with the usage block."""
        body = self._body(prompt, max_tokens, temperature)
        started = time.monotonic()
        try:
            payload = self._post(body, timeout)
        except GeneratorError as exc:
            # Newer OpenAI models reject max_tokens in favour of max_completion_tokens: retry once.
            if "max_tokens" in str(exc) and "max_completion_tokens" in str(exc):
                body.pop("max_tokens", None)
                body["max_completion_tokens"] = max_tokens
                payload = self._post(body, timeout)
            else:
                raise
        wall_ms = int(round((time.monotonic() - started) * 1000))
        if not isinstance(payload, dict):
            raise GeneratorError("response is not a JSON object")
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise GeneratorError("response has no choices")
        message = choices[0].get("message") or {}
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str):
            raise GeneratorError("response has no text content")
        raw_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        usage = {k: int(v) for k, v in raw_usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        return GeneratorResult(text=text, model=str(payload.get("model") or self.model), recorded=False,
                               prompt_sha256=prompt_sha256(prompt), usage=usage, provider=self.provider,
                               requested_model=self.model, routing=self._routing(payload, wall_ms))

    def _routing(self, payload: dict[str, Any], wall_ms: int) -> dict[str, Any]:
        """Routing facts of one answer (none for a plain OpenAI-compatible endpoint)."""
        return {}


class NightshiftGenerator(OpenAIGenerator):
    """Meterless Nightshift (Meterless Runtime, OpenAI-compatible provider `meterless`): the primary generator.

    Same chat-completions call as OpenAIGenerator plus `meterless-app` (every request) and `meterless-scope: user`
    (chat requests); the model id is a routing mode (meterless/auto by default) or a concrete model id that pins the
    route (nvidia/nemotron-3.5-lightning-30b-a3b), the timeout 90 s. `reasoning_effort` (NIGHTSHIFT_REASONING_EFFORT,
    default none) adds the call options of nightshift_call_options. The token is the runtime's proxy-token: sent as
    the Bearer value, never logged. `mount_info` (no token) is what run_start logs."""

    provider = NIGHTSHIFT_PROVIDER_ID
    timeout_s = NIGHTSHIFT_TIMEOUT_S

    def __init__(self, token: str, base_url: str = NIGHTSHIFT_DEFAULT_BASE_URL, model: str = NIGHTSHIFT_DEFAULT_MODEL,
                 opener: Optional[Any] = None, models: Optional[list[str]] = None, found_by: str = "",
                 token_source: str = "", reasoning_effort: Optional[str] = NIGHTSHIFT_DEFAULT_REASONING_EFFORT) -> None:
        super().__init__(token, normalize_nightshift_url(base_url), model or NIGHTSHIFT_DEFAULT_MODEL, opener=opener)
        self.name = f"nightshift:{self.model}"
        self.models = list(models or [])
        self.call_options, note = nightshift_call_options(reasoning_effort)
        self.mount_info: dict[str, Any] = {"provider": self.provider, "base_url": self.base_url,
                                           "routing_mode": self.model, "found_by": found_by or "configured",
                                           "models": self.models, "timeout_s": self.timeout_s,
                                           "call_options": dict(self.call_options)}
        if note:
            self.mount_info["call_options_note"] = note
        if token_source:                     # where the token came from (variable names only, never the value)
            self.mount_info["token_source"] = token_source

    def _body(self, prompt: str, max_tokens: int, temperature: float) -> dict[str, Any]:
        """The OpenAI body plus the call options: the `/no_think` system turn before the prompt (the prompt stays
        the one user turn, byte for byte) and `reasoning_effort`."""
        body = super()._body(prompt, max_tokens, temperature)
        system_turn = self.call_options.get("system_turn")
        if system_turn:
            body["messages"] = [{"role": "system", "content": system_turn}] + body["messages"]
        if self.call_options.get("reasoning_effort"):
            body["reasoning_effort"] = self.call_options["reasoning_effort"]
        return body

    def _headers(self) -> dict[str, str]:
        headers = nightshift_headers(self._api_key)
        headers.update({"Content-Type": "application/json", "meterless-scope": "user"})
        return headers

    def _routing(self, payload: dict[str, Any], wall_ms: int) -> dict[str, Any]:
        return meterless_receipt(payload, wall_ms)


def nightshift_call_options(reasoning_effort: Optional[str]) -> tuple[dict[str, Any], str]:
    """(call options, note) of every Nightshift chat request for NIGHTSHIFT_REASONING_EFFORT (config.read_env).

    - `none` (the default): `reasoning_effort: "none"` in the body, which Nightshift 1.2.3 turns into
      chat_template_kwargs.enable_thinking=false for its NVIDIA-hosted Nemotron 3 / 3.5 rows (engine providers.js,
      nvidiaThinkingBody), and a system turn `/no_think` before the prompt: the switch the chat template of the
      local Nemotron Nano 9B v2 GGUF reads (it ignores enable_thinking) and deletes, so the rendered system turn is
      empty. Checked on 2026-10-24 (examples/live-trace-nightshift/nemotron-*/smoke/): without them both Nemotron
      routes thought through the Critic budget (500 tokens; the hosted one also 3000) and Nightshift answered HTTP 502
      empty_response; with them every request of the Nemotron live runs was answered.
    - minimal | low | medium | high | xhigh: `reasoning_effort` only (rows that take an effort, such as OpenAI's
      Responses models; the NVIDIA adapter of Nightshift acts on `none` alone).
    - `default`: nothing is added; each route keeps its own thinking setting (Nightshift Preferences > NVIDIA thinking).
    Any other value is treated as `default` and the note says so (it is never sent)."""
    value = (reasoning_effort or "").strip().lower() or NIGHTSHIFT_DEFAULT_REASONING_EFFORT
    if value not in NIGHTSHIFT_REASONING_EFFORTS:
        return {}, (f"NIGHTSHIFT_REASONING_EFFORT={value[:20]!r} is not one of {', '.join(NIGHTSHIFT_REASONING_EFFORTS)}: "
                    "treated as default, nothing sent")
    if value == "default":
        return {}, ""
    if value == "none":
        return {"reasoning_effort": "none", "system_turn": NO_THINK_SYSTEM_TURN}, ""
    return {"reasoning_effort": value}, ""


RECEIPT_ROUTE_KEYS: tuple[str, ...] = ("model", "label", "provider", "kind", "tier")
RECEIPT_USAGE_KEYS: tuple[str, ...] = ("input_tokens", "output_tokens", "total_tokens")
RECEIPT_TEXT_MAX = 160


def _receipt_text(value: Any) -> Optional[str]:
    """A short receipt string (ids, model names, labels); anything else -> None."""
    if isinstance(value, str) and value.strip():
        return value.strip()[:RECEIPT_TEXT_MAX]
    return None


def _receipt_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def meterless_receipt(payload: dict[str, Any], wall_ms: Optional[int] = None) -> dict[str, Any]:
    """The routing facts of a Nightshift answer, from its `meterless` compute receipt (meterless.compute_receipt.v1):
    receipt_id, decision_id, requested_model (routing mode), routed_model (the model that answered),
    winning_route {model, label, provider, kind, tier}, phases [{phase, model, ms}], latency_ms (the receipt's) and
    usage {input_tokens, output_tokens, total_tokens}, cache status, from_cache, privacy path, plus wall_ms (the
    request as this process timed it). Only those keys are copied: the receipt's prompt_preview, cost and host
    telemetry stay out of the logs. A response without the object gives {} (wall_ms alone is not routing)."""
    receipt = payload.get("meterless") if isinstance(payload, dict) else None
    if not isinstance(receipt, dict):
        return {}
    out: dict[str, Any] = {"source": "meterless_receipt"}
    for key in ("schema", "receipt_id", "decision_id", "requested_model", "routed_model"):
        text = _receipt_text(receipt.get(key))
        if text:
            out[key] = text
    route = receipt.get("winning_route")
    if isinstance(route, dict):
        kept = {k: _receipt_text(route.get(k)) for k in RECEIPT_ROUTE_KEYS}
        kept = {k: v for k, v in kept.items() if v}
        if kept:
            out["winning_route"] = kept
    phases = []
    for phase in receipt.get("phases") if isinstance(receipt.get("phases"), list) else []:
        if not isinstance(phase, dict):
            continue
        item = {"phase": _receipt_text(phase.get("phase")), "model": _receipt_text(phase.get("model")),
                "ms": _receipt_int(phase.get("ms"))}
        item = {k: v for k, v in item.items() if v is not None}
        if item:
            phases.append(item)
    if phases:
        out["phases"] = phases[:10]
    usage = receipt.get("usage") if isinstance(receipt.get("usage"), dict) else {}
    latency = _receipt_int(usage.get("latency_ms"))
    if latency is not None:
        out["latency_ms"] = latency
    tokens = {k: _receipt_int(usage.get(k)) for k in RECEIPT_USAGE_KEYS}
    tokens = {k: v for k, v in tokens.items() if v is not None}
    if tokens:
        out["usage"] = tokens
    cache = receipt.get("cache")
    if isinstance(cache, dict) and _receipt_text(cache.get("status")):
        out["cache"] = _receipt_text(cache.get("status"))
    if isinstance(receipt.get("from_cache"), bool):
        out["from_cache"] = receipt["from_cache"]
    privacy = receipt.get("privacy")
    if isinstance(privacy, dict) and _receipt_text(privacy.get("path")):
        out["privacy_path"] = _receipt_text(privacy.get("path"))
    if wall_ms is not None:
        out["wall_ms"] = int(wall_ms)
    return out


# ----------------------------------------------------------------------------------------------------------
# Nightshift: URL normalisation, token, status, Auto Detect
# ----------------------------------------------------------------------------------------------------------

def normalize_nightshift_url(raw: Optional[str]) -> str:
    """A pasted runtime URL -> <scheme>://<host>[:port]/v1. Accepts it with or without a scheme and ending in /v1,
    /v1/chat/completions, /v1/models or /healthz (with or without a trailing slash); never produces /v1/v1. Empty ->
    the default http://127.0.0.1:8088/v1. Query, fragment and user info are dropped (nothing secret is kept)."""
    text = (raw or "").strip()
    if not text:
        return NIGHTSHIFT_DEFAULT_BASE_URL
    if "://" not in text:
        text = "http://" + text.lstrip("/")
    parts = urllib.parse.urlsplit(text)
    scheme = (parts.scheme or "http").lower()
    host = parts.hostname or "127.0.0.1"
    if ":" in host:                                   # IPv6 literal: keep the brackets
        host = f"[{host}]"
    netloc = f"{host}:{parts.port}" if parts.port else host
    path = parts.path.rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in ("/chat/completions", "/models", "/healthz", "/v1"):
            if path.lower().endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
    return f"{scheme}://{netloc}{path}/v1"


def nightshift_headers(token: Optional[str]) -> dict[str, str]:
    """Headers every Nightshift request carries: meterless-app always, the Bearer token when there is one."""
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT, "meterless-app": NIGHTSHIFT_APP_ID}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


TOKEN_VARS_TEXT = "NIGHTSHIFT_TOKEN / METERLESS_RUNTIME_TOKEN / METERLESS_TOKEN"


def resolve_nightshift_token(env: Env) -> tuple[Optional[str], str]:
    """(token, where it came from), first found: the token variables (NIGHTSHIFT_TOKEN, METERLESS_RUNTIME_TOKEN,
    METERLESS_TOKEN; Env keeps the first one set), the file at NIGHTSHIFT_TOKEN_FILE, then <stateDir>/proxy-token
    (the file the runtime itself writes; stateDir = METERLESS_STATE_DIR or the OS default, config.meterless_state_dir),
    else none. The second value names variables only, never the token or a path, and is safe to log. The
    placeholder rt-local is never sent (the runtime rejects it)."""
    if env.nightshift_token and env.nightshift_token.strip():
        token, source = env.nightshift_token.strip(), env.nightshift_token_var or "NIGHTSHIFT_TOKEN"
    elif env.nightshift_token_file:
        try:
            token = Path(env.nightshift_token_file).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            return None, f"NIGHTSHIFT_TOKEN_FILE could not be read ({type(exc).__name__})"
        source = "NIGHTSHIFT_TOKEN_FILE"
    elif env.nightshift_state_dir:
        where = "METERLESS_STATE_DIR" if env.nightshift_state_dir_var == "METERLESS_STATE_DIR" else "the default stateDir"
        source = f"<stateDir>/proxy-token ({'METERLESS_STATE_DIR' if where == 'METERLESS_STATE_DIR' else 'default stateDir'})"
        path = Path(env.nightshift_state_dir).expanduser() / "proxy-token"
        if not path.is_file():
            return None, (f"no token configured ({TOKEN_VARS_TEXT} and NIGHTSHIFT_TOKEN_FILE unset, no proxy-token in "
                          f"{where})")
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            return None, f"{source} could not be read ({type(exc).__name__})"
    else:
        return None, f"no token configured ({TOKEN_VARS_TEXT} / NIGHTSHIFT_TOKEN_FILE unset, no stateDir known)"
    if not token:
        return None, f"{source} is empty"
    if token == NIGHTSHIFT_PLACEHOLDER_TOKEN:
        return None, f"{source} is the placeholder {NIGHTSHIFT_PLACEHOLDER_TOKEN} (the runtime rejects it)"
    return token, source


@dataclass
class NightshiftProbe:
    """One status decision from GET <base_url>/models (the token is never part of it)."""

    base_url: str
    status: NightshiftStatus
    http: Optional[int] = None
    models: list[str] = field(default_factory=list)   # meterless/* ids as listed, unknown ones kept
    detail: str = ""


def probe_nightshift(base_url: str, token: Optional[str] = None, opener: Optional[Any] = None,
                     timeout: float = NIGHTSHIFT_PROBE_TIMEOUT_S) -> NightshiftProbe:
    """GET <base>/models decides: 401/403 -> unauthorized; 200 with meterless/* ids -> online; anything else
    (other status, no meterless id, not JSON, refused, timeout) -> offline. /healthz is not consulted: it only
    proves the process is up. A NetworkDisabledError (the sample-mode guard) is not caught."""
    base = normalize_nightshift_url(base_url)
    request = urllib.request.Request(f"{base}/models", method="GET", headers=nightshift_headers(token))
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            code = int(getattr(response, "status", None) or getattr(response, "code", None) or 200)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status: NightshiftStatus = "unauthorized" if exc.code in (401, 403) else "offline"
        return NightshiftProbe(base, status, exc.code, detail=f"HTTP {exc.code} on GET /v1/models")
    except urllib.error.URLError as exc:
        return NightshiftProbe(base, "offline", detail=f"connection error: {exc.reason}")
    except (TimeoutError, OSError, ValueError) as exc:
        return NightshiftProbe(base, "offline", detail=f"{type(exc).__name__}: {exc}")
    if code != 200:
        status = "unauthorized" if code in (401, 403) else "offline"
        return NightshiftProbe(base, status, code, detail=f"HTTP {code} on GET /v1/models")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return NightshiftProbe(base, "offline", code, detail="GET /v1/models did not answer JSON")
    items = payload.get("data") if isinstance(payload, dict) else payload
    ids = [str(item.get("id")) for item in (items if isinstance(items, list) else [])
           if isinstance(item, dict) and item.get("id")]
    models = [i for i in ids if i.startswith("meterless/")]
    if not models:
        return NightshiftProbe(base, "offline", code,
                               detail="GET /v1/models lists no meterless/* id (not a Meterless Runtime)")
    return NightshiftProbe(base, "online", code, models=models, detail=f"{len(models)} routing mode(s) listed")


def nightshift_candidates() -> list[str]:
    """Auto Detect order: each port (8088 first) on 127.0.0.1, localhost and [::1]."""
    return [f"http://{host}:{port}/v1" for port in NIGHTSHIFT_DETECT_PORTS for host in NIGHTSHIFT_DETECT_HOSTS]


def detect_nightshift(token: Optional[str] = None, *, base_url: Optional[str] = None, opener: Optional[Any] = None,
                      timeout: float = NIGHTSHIFT_PROBE_TIMEOUT_S,
                      candidates: Optional[list[str]] = None) -> NightshiftProbe:
    """Status of the Nightshift runtime. With base_url: that runtime only. Without: Auto Detect probes every
    candidate (in parallel, so refused ports cost one timeout at most) and keeps the first online or unauthorized
    hit in candidate order; none -> offline at the default URL. Never called in sample mode (build_generator)."""
    if base_url:
        return probe_nightshift(base_url, token, opener=opener, timeout=timeout)
    urls = list(candidates or nightshift_candidates())
    with ThreadPoolExecutor(max_workers=max(1, len(urls))) as pool:
        probes = list(pool.map(lambda url: probe_nightshift(url, token, opener=opener, timeout=timeout), urls))
    for probe in probes:
        if probe.status in ("online", "unauthorized"):
            return probe
    ports, hosts = ", ".join(str(p) for p in NIGHTSHIFT_DETECT_PORTS), ", ".join(NIGHTSHIFT_DETECT_HOSTS)
    return NightshiftProbe(NIGHTSHIFT_DEFAULT_BASE_URL, "offline",
                           detail=f"Auto Detect: no Meterless Runtime on ports {ports} of {hosts}")


def nightshift_unavailable_reason(probe: Optional[NightshiftProbe], explicit: bool, token_source: str,
                                  base_url_var: str = "NIGHTSHIFT_BASE_URL") -> str:
    """One loggable sentence (variable names only) saying why Nightshift is not the mounted generator."""
    if probe is None:
        return "Nightshift not configured: NIGHTSHIFT_BASE_URL / METERLESS_BASE_URL is unset and NIGHTSHIFT_AUTODETECT=0"
    where = f"{probe.base_url} ({base_url_var if explicit else 'auto-detected'})"
    if probe.status == "unauthorized":
        return (f"Nightshift at {where} is unauthorized ({probe.detail}); token: {token_source}; set NIGHTSHIFT_TOKEN "
                "(or METERLESS_RUNTIME_TOKEN) or NIGHTSHIFT_TOKEN_FILE to the runtime's <stateDir>/proxy-token")
    if explicit:
        return f"Nightshift at {where} is offline: {probe.detail}"
    return f"Nightshift not found: {probe.detail}"


# ----------------------------------------------------------------------------------------------------------
# Mounting
# ----------------------------------------------------------------------------------------------------------

def build_generator(config: RunConfig, example_name: Optional[str] = None, *, opener: Optional[Any] = None) -> Generator:
    """Mount the generator for this run.

    Sample mode: recorded (when fixtures/transcripts/<example>/ has transcripts) | null; no network and no Nightshift
    detection. Live mode, in order: Nightshift when its runtime is online (NIGHTSHIFT_BASE_URL / METERLESS_BASE_URL,
    else Auto Detect unless NIGHTSHIFT_AUTODETECT=0) -> the OpenAI-compatible generator when OPENAI_API_KEY is set -> null. A live
    generator other than Nightshift carries `nightshift_unavailable` (the reason), which the runner logs as
    recovery{nightshift_unavailable}. `opener` is the urlopen-compatible transport (tests)."""
    if config.is_sample():
        if example_name:
            folder = config.transcripts_dir / example_name
            if folder.is_dir() and any(folder.glob("*.json")):
                return RecordedGenerator(folder)
        return NullGenerator()
    env = config.env
    explicit = bool(env.nightshift_base_url)
    token, token_source = resolve_nightshift_token(env)
    probe: Optional[NightshiftProbe] = None
    if explicit or env.nightshift_autodetect:
        probe = detect_nightshift(token, base_url=env.nightshift_base_url, opener=opener)
        if probe.status == "online":
            found_by = env.nightshift_base_url_var if explicit else "auto_detect"
            return NightshiftGenerator(token or "", probe.base_url, env.nightshift_model, opener=opener,
                                       models=probe.models, found_by=found_by,
                                       token_source=token_source if token else "none",
                                       reasoning_effort=env.nightshift_reasoning_effort)
    fallback: Any
    if env.has_generator():
        fallback = OpenAIGenerator(env.openai_api_key or "", env.openai_base_url, env.model, opener=opener)
    else:
        fallback = NullGenerator()
    fallback.nightshift_unavailable = {
        "detail": nightshift_unavailable_reason(probe, explicit, token_source, env.nightshift_base_url_var),
        "status": probe.status if probe else "not_configured",
        "base_url": probe.base_url if probe else None}
    return fallback


def save_transcript(folder: Path, prompt_id: str, prompt: str, result: GeneratorResult, recorded_at: str,
                    extra: Optional[dict[str, Any]] = None) -> Path:
    """Write <prompt_id>-<sha12>.json and the prompt text beside it (used by --record-transcripts and
    scripts/record_transcripts.py). `extra`: provenance keys (recorded_in_run, prompt_context); replays ignore them."""
    folder.mkdir(parents=True, exist_ok=True)
    name = transcript_filename(prompt_id, prompt)
    record: dict[str, Any] = {"prompt_id": prompt_id, "prompt_sha256": result.prompt_sha256, "model": result.model,
                              "recorded_at": recorded_at, "response": result.text, "usage": result.usage}
    if result.provider:
        record["provider"] = result.provider
    if result.requested_model:
        record["requested_model"] = result.requested_model
    if result.routing:
        record["routing"] = result.routing
    record.update(extra or {})
    path = folder / name
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (folder / name.replace(".json", ".prompt.txt")).write_text(prompt, encoding="utf-8", newline="\n")
    return path


def render_prompt(template: str, values: dict[str, Any]) -> str:
    """Fill {{name}} slots; unknown slots are left empty so a prompt never carries raw template markers."""
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", "" if value is None else str(value))
    return re.sub(r"\{\{[a-z_]+\}\}", "", text)


def load_prompt(name: str) -> str:
    path = Path(__file__).resolve().parent / "agents" / "prompts" / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def log_injection(ctx: "RunContext", agent: str, prompt_id: str, prompt: str, reinjection: str,
                  memory_ids: list[str], attempt: int) -> None:
    """Record that an H-MEM reinjection block is inside the prompt handed to the generator (run.jsonl + trace)."""
    if not reinjection or reinjection not in prompt:
        return
    details = {"prompt_id": prompt_id, "attempt": attempt, "prompt_sha256": prompt_sha256(prompt),
               "reinjection_sha256": hashlib.sha256(reinjection.encode("utf-8")).hexdigest()}
    seq = ctx.log.emit("memory", agent, {"op": "inject", "actor": agent, "memory_ids": list(memory_ids), "details": details})
    ctx.log.side("hmem.trace", {"seq": seq, "actor": agent, "op": "inject", "memoryIds": list(memory_ids), **details})


def generate(ctx: "RunContext", agent: str, prompt_id: str, prompt: str, *, max_tokens: int,
             attempt: int = 1, reinjection: str = "", memory_ids: Optional[list[str]] = None) -> Optional[GeneratorResult]:
    """One bounded generator call for `agent`. None means "use the deterministic fallback" (already logged).
    `reinjection` / `memory_ids`: the H-MEM block filled into the prompt, traced as an `inject` memory event."""
    generator = ctx.generator
    if generator is None or generator.name == "null":
        return None
    if ctx.time_left_ms() < MIN_SECONDS_LEFT_FOR_GENERATOR * 1000:
        ctx.recover(agent, "deadline_skip", f"generator pass {prompt_id} skipped: {ctx.time_left_ms() // 1000} s left")
        return None
    if not ctx.budget_take("generator_calls", ctx.budget.max_generator_calls, agent):
        return None
    ctx.counters["generator_attempts"] = ctx.counters.get("generator_attempts", 0) + 1
    log_injection(ctx, agent, prompt_id, prompt, reinjection, list(memory_ids or []), attempt)
    # The generator's own timeout (Nightshift 90 s), ending at least GENERATOR_RESERVE_S before the deadline so the
    # fallback and finalize still have time (at least MIN_SECONDS_LEFT_FOR_GENERATOR are left here).
    timeout = min(float(getattr(generator, "timeout_s", GENERATOR_TIMEOUT_S)),
                  ctx.time_left_ms() / 1000 - GENERATOR_RESERVE_S)
    route = {"generator": generator.name, "provider": getattr(generator, "provider", generator.name),
             "requested_model": getattr(generator, "model", None)}
    try:
        result = generator.complete(prompt_id, prompt, max_tokens=max_tokens, temperature=GENERATOR_TEMPERATURE,
                                    timeout=timeout)
    except GeneratorError as exc:
        if ctx.config.require_generator:
            raise InvestigatorError("E_GENERATOR", agent, f"generator call {prompt_id} failed: {exc}", retryable=True,
                                    details={"prompt_id": prompt_id, "prompt_sha256": prompt_sha256(prompt), **route})
        ctx.fallback_used = ctx.fallback_used or ctx.config.mode == "live"
        ctx.recover(agent, "generator_fallback", f"{prompt_id}: {exc}", to="rules/template", **route)
        return None
    if result is None:
        detail = "no recorded transcript" if generator.name == "recorded" else "empty response"
        ctx.recover(agent, "generator_fallback", f"{prompt_id}: {detail}", to="rules/template", **route)
        return None
    response_sha = hashlib.sha256(result.text.encode("utf-8")).hexdigest()
    call_options = dict(getattr(generator, "call_options", None) or {}) if not result.recorded else {}
    request = {"max_tokens": max_tokens, **({"call_options": call_options} if call_options else {})}
    folder = ctx.run_dir / "transcripts"
    path = save_transcript(folder, prompt_id, prompt, result, ctx.clock.now_iso(),
                           extra=None if result.recorded else {"request": request})
    if ctx.config.record_transcripts and not result.recorded:
        example = ctx.config.example_name or ctx.example_name
        if example:
            save_transcript(ctx.config.transcripts_dir / example, prompt_id, prompt, result, ctx.clock.now_iso(),
                            extra={"recorded_in_run": ctx.run_id, "prompt_context": f"live run {ctx.run_id}",
                                   "request": request})
    # Which provider and model handled this request: one run.jsonl `generate` event, and generator.jsonl repeats
    # its seq with the call's parameters and the transcript path. A Nightshift answer adds `routing`, the facts of
    # its compute receipt (receipt id, routed model, winning route, latency, tokens); a replay repeats the recorded
    # call's routing with recorded: true.
    event = {"prompt_id": prompt_id, "attempt": attempt, "generator": generator.name,
             "provider": result.provider or route["provider"],
             "requested_model": result.requested_model or route["requested_model"], "model": result.model,
             "recorded": result.recorded, "prompt_sha256": result.prompt_sha256, "response_sha256": response_sha}
    if result.routing:
        event["routing"] = result.routing
    seq = ctx.log.emit("generate", agent, event)
    line = {"seq": seq, "agent": agent, **event, "max_tokens": max_tokens, "temperature": GENERATOR_TEMPERATURE,
            "timeout_s": round(timeout, 3), "usage": result.usage,
            "transcript": str(path.relative_to(ctx.run_dir)).replace("\\", "/")}
    if call_options:     # the thinking switch this request carried (NIGHTSHIFT_REASONING_EFFORT)
        line["call_options"] = call_options
    ctx.log.side("generator", line)
    ctx.scratch.setdefault("generator_calls", []).append(line)
    return result


def parse_json_object(text: str) -> Optional[dict[str, Any]]:
    """Strict JSON object from a model reply; tolerant of code fences and leading prose. None when absent."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse_json_list(text: str) -> Optional[list[Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def salvage_json_list(text: str) -> tuple[Optional[list[Any]], int]:
    """parse_json_list, else the complete leading items of a JSON array the model cut off (a reply that hit its
    token limit mid-array, as the 3B lane of 2026-10-24 did): (items, 0) for a whole array, (items, n) when n
    complete items were read from a truncated one, (None, 0) when there is no array at all. Items are decoded with
    json's own decoder, one by one; the first incomplete or malformed item ends the salvage (nothing is guessed)."""
    whole = parse_json_list(text)
    if whole is not None:
        return whole, 0
    start = text.find("[")
    if start < 0:
        return None, 0
    decoder = json.JSONDecoder()
    items: list[Any] = []
    pos = start + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] == "]":
            break
        try:
            item, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        items.append(item)
    return (items, len(items)) if items else (None, 0)
