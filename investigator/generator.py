"""Generator protocol implementations (SPEC.md section 3 and 7.4 rule 5). Owner: B4.

Exactly three roles are generator-backed (Planner, Writer, Critic second opinion); every other agent is tool-only.
- NightshiftGenerator: Meterless Nightshift, the PRIMARY provider in live mode. The Meterless Runtime (0.90.0) is an
  OpenAI-compatible provider (id `meterless`, default http://127.0.0.1:8088/v1): Bearer token from NIGHTSHIFT_TOKEN
  or the file NIGHTSHIFT_TOKEN_FILE (<stateDir>/proxy-token), `meterless-app: x402-adoption-investigator` on every
  request and `meterless-scope: user` on chat requests, routing mode NIGHTSHIFT_MODEL (default meterless/auto),
  180 s timeout (cold model loads take minutes). detect_nightshift() decides its status from GET /v1/models
  (401/403 unauthorized, 200 with meterless/* ids online, anything else offline) at NIGHTSHIFT_BASE_URL, or by Auto
  Detect over ports 8088, 8090, 8787 and 11434 on 127.0.0.1, localhost and [::1].
- OpenAIGenerator: OpenAI-compatible chat completions over urllib, the fallback when Nightshift is not online.
  Injected by env: OPENAI_API_KEY + OPENAI_BASE_URL + MODEL. Never the Codex/ChatGPT subscription.
- RecordedGenerator: replays fixtures/transcripts/<example>/<prompt_id>-<prompt_sha12>.json (sample mode).
- NullGenerator: always returns None; callers fall back to rules/templates and log a recovery.
A generator never approves a claim and never picks the verdict label. build_generator() picks the mount: sample
mode -> recorded | null with no network call and no detection; live mode -> nightshift, else openai, else null, and
a live run that does not mount Nightshift logs recovery{nightshift_unavailable} with the probe's reason.

generate(ctx, ...) is the single entry point the agents use: it enforces budget.max_generator_calls, uses
temperature 0 and the generator's timeout (60 s, Nightshift 180 s, never past the run's deadline), saves every
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
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from . import USER_AGENT
from .config import NIGHTSHIFT_DEFAULT_MODEL, Env, RunConfig
from .contracts import Generator, GeneratorResult
from .errors import InvestigatorError

if TYPE_CHECKING:
    from .runner import RunContext

GENERATOR_TEMPERATURE = 0.0
GENERATOR_TIMEOUT_S = 60.0
MIN_SECONDS_LEFT_FOR_GENERATOR = 20

NIGHTSHIFT_PROVIDER_ID = "meterless"
NIGHTSHIFT_APP_ID = "x402-adoption-investigator"          # meterless-app header: stable lowercase app id
NIGHTSHIFT_DEFAULT_BASE_URL = "http://127.0.0.1:8088/v1"
NIGHTSHIFT_TIMEOUT_S = 180.0                               # chat calls: a cold model load can take minutes
NIGHTSHIFT_PROBE_TIMEOUT_S = 3.0                           # one GET /v1/models during detection
NIGHTSHIFT_DETECT_PORTS: tuple[int, ...] = (8088, 8090, 8787, 11434)
NIGHTSHIFT_DETECT_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
NIGHTSHIFT_PLACEHOLDER_TOKEN = "rt-local"                  # rejected by the runtime: never sent
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
        return GeneratorResult(text=str(record.get("response", "")), model=str(record.get("model", "recorded")),
                               recorded=True, prompt_sha256=digest, usage=dict(record.get("usage") or {}),
                               provider=str(record.get("provider") or "recorded"),
                               requested_model=record.get("requested_model"))


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

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]:
        """POST {base_url}/chat/completions; returns the first choice's text with the usage block."""
        body: dict[str, Any] = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                                "temperature": temperature, "max_tokens": max_tokens}
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
        choices = payload.get("choices") or []
        if not choices:
            raise GeneratorError("response has no choices")
        message = choices[0].get("message") or {}
        text = message.get("content")
        if not isinstance(text, str):
            raise GeneratorError("response has no text content")
        usage = {k: int(v) for k, v in (payload.get("usage") or {}).items() if isinstance(v, (int, float))}
        return GeneratorResult(text=text, model=str(payload.get("model") or self.model), recorded=False,
                               prompt_sha256=prompt_sha256(prompt), usage=usage, provider=self.provider,
                               requested_model=self.model)


class NightshiftGenerator(OpenAIGenerator):
    """Meterless Nightshift (Meterless Runtime, OpenAI-compatible provider `meterless`): the primary generator.

    Same chat-completions call as OpenAIGenerator plus `meterless-app` (every request) and `meterless-scope: user`
    (chat requests); the model id is a routing mode (meterless/auto by default), the timeout 180 s. The token is the
    runtime's proxy-token: sent as the Bearer value, never logged. `mount_info` (no token) is what run_start logs."""

    provider = NIGHTSHIFT_PROVIDER_ID
    timeout_s = NIGHTSHIFT_TIMEOUT_S

    def __init__(self, token: str, base_url: str = NIGHTSHIFT_DEFAULT_BASE_URL, model: str = NIGHTSHIFT_DEFAULT_MODEL,
                 opener: Optional[Any] = None, models: Optional[list[str]] = None, found_by: str = "") -> None:
        super().__init__(token, normalize_nightshift_url(base_url), model or NIGHTSHIFT_DEFAULT_MODEL, opener=opener)
        self.name = f"nightshift:{self.model}"
        self.models = list(models or [])
        self.mount_info: dict[str, Any] = {"provider": self.provider, "base_url": self.base_url,
                                           "routing_mode": self.model, "found_by": found_by or "configured",
                                           "models": self.models, "timeout_s": self.timeout_s}

    def _headers(self) -> dict[str, str]:
        headers = nightshift_headers(self._api_key)
        headers.update({"Content-Type": "application/json", "meterless-scope": "user"})
        return headers


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


def resolve_nightshift_token(env: Env) -> tuple[Optional[str], str]:
    """(token, where it came from). NIGHTSHIFT_TOKEN, else the file at NIGHTSHIFT_TOKEN_FILE (the runtime writes it
    to <stateDir>/proxy-token), else none. The second value names variables only, never the token or the path, and
    is safe to log. The placeholder rt-local is never sent (the runtime rejects it)."""
    if env.nightshift_token:
        token, source = env.nightshift_token.strip(), "NIGHTSHIFT_TOKEN"
    elif env.nightshift_token_file:
        try:
            token = Path(env.nightshift_token_file).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            return None, f"NIGHTSHIFT_TOKEN_FILE could not be read ({type(exc).__name__})"
        source = "NIGHTSHIFT_TOKEN_FILE"
    else:
        return None, "no token configured (NIGHTSHIFT_TOKEN / NIGHTSHIFT_TOKEN_FILE unset)"
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


def nightshift_unavailable_reason(probe: Optional[NightshiftProbe], explicit: bool, token_source: str) -> str:
    """One loggable sentence (variable names only) saying why Nightshift is not the mounted generator."""
    if probe is None:
        return "Nightshift not configured: NIGHTSHIFT_BASE_URL is unset and NIGHTSHIFT_AUTODETECT=0"
    where = f"{probe.base_url} ({'NIGHTSHIFT_BASE_URL' if explicit else 'auto-detected'})"
    if probe.status == "unauthorized":
        return (f"Nightshift at {where} is unauthorized ({probe.detail}); token: {token_source}; set NIGHTSHIFT_TOKEN "
                "or NIGHTSHIFT_TOKEN_FILE to the runtime's <stateDir>/proxy-token")
    if explicit:
        return f"Nightshift at {where} is offline: {probe.detail}"
    return f"Nightshift not found: {probe.detail}"


# ----------------------------------------------------------------------------------------------------------
# Mounting
# ----------------------------------------------------------------------------------------------------------

def build_generator(config: RunConfig, example_name: Optional[str] = None, *, opener: Optional[Any] = None) -> Generator:
    """Mount the generator for this run.

    Sample mode: recorded (when fixtures/transcripts/<example>/ has transcripts) | null; no network and no Nightshift
    detection. Live mode, in order: Nightshift when its runtime is online (NIGHTSHIFT_BASE_URL, else Auto Detect
    unless NIGHTSHIFT_AUTODETECT=0) -> the OpenAI-compatible generator when OPENAI_API_KEY is set -> null. A live
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
            found_by = "NIGHTSHIFT_BASE_URL" if explicit else "auto_detect"
            return NightshiftGenerator(token or "", probe.base_url, env.nightshift_model, opener=opener,
                                       models=probe.models, found_by=found_by)
    fallback: Any
    if env.has_generator():
        fallback = OpenAIGenerator(env.openai_api_key or "", env.openai_base_url, env.model, opener=opener)
    else:
        fallback = NullGenerator()
    fallback.nightshift_unavailable = {
        "detail": nightshift_unavailable_reason(probe, explicit, token_source),
        "status": probe.status if probe else "not_configured",
        "base_url": probe.base_url if probe else None}
    return fallback


def save_transcript(folder: Path, prompt_id: str, prompt: str, result: GeneratorResult, recorded_at: str) -> Path:
    """Write <prompt_id>-<sha12>.json and the prompt text beside it (used by --record-transcripts)."""
    folder.mkdir(parents=True, exist_ok=True)
    name = transcript_filename(prompt_id, prompt)
    record: dict[str, Any] = {"prompt_id": prompt_id, "prompt_sha256": result.prompt_sha256, "model": result.model,
                              "recorded_at": recorded_at, "response": result.text, "usage": result.usage}
    if result.provider:
        record["provider"] = result.provider
    if result.requested_model:
        record["requested_model"] = result.requested_model
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
    # The generator's own timeout (Nightshift 180 s), never past the run's deadline (at least 20 s are left here).
    timeout = min(float(getattr(generator, "timeout_s", GENERATOR_TIMEOUT_S)), ctx.time_left_ms() / 1000)
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
    folder = ctx.run_dir / "transcripts"
    path = save_transcript(folder, prompt_id, prompt, result, ctx.clock.now_iso())
    if ctx.config.record_transcripts and not result.recorded:
        example = ctx.config.example_name or ctx.example_name
        if example:
            save_transcript(ctx.config.transcripts_dir / example, prompt_id, prompt, result, ctx.clock.now_iso())
    # Which provider and model handled this request: one run.jsonl `generate` event, and generator.jsonl repeats
    # its seq with the call's parameters and the transcript path.
    event = {"prompt_id": prompt_id, "attempt": attempt, "generator": generator.name,
             "provider": result.provider or route["provider"],
             "requested_model": result.requested_model or route["requested_model"], "model": result.model,
             "recorded": result.recorded, "prompt_sha256": result.prompt_sha256, "response_sha256": response_sha}
    seq = ctx.log.emit("generate", agent, event)
    line = {"seq": seq, "agent": agent, **event, "max_tokens": max_tokens, "temperature": GENERATOR_TEMPERATURE,
            "timeout_s": round(timeout, 3), "usage": result.usage,
            "transcript": str(path.relative_to(ctx.run_dir)).replace("\\", "/")}
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
