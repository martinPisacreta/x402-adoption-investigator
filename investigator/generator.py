"""Generator protocol implementations (SPEC.md section 3 and 7.4 rule 5). Owner: B4.

Exactly three roles are generator-backed (Planner, Writer, Critic second opinion); every other agent is tool-only.
- OpenAIGenerator: OpenAI-compatible chat completions over urllib (live mode). Injected by env:
  OPENAI_API_KEY + OPENAI_BASE_URL + MODEL. Never the Codex/ChatGPT subscription.
- RecordedGenerator: replays fixtures/transcripts/<example>/<prompt_id>-<prompt_sha12>.json (sample mode).
- NullGenerator: always returns None; callers fall back to rules/templates and log a recovery.
A generator never approves a claim and never picks the verdict label.

generate(ctx, ...) is the single entry point the agents use: it enforces budget.max_generator_calls, uses
temperature 0 and a 60 s timeout, saves every transcript under logs/<run_id>/transcripts/ (and, with
--record-transcripts, under fixtures/transcripts/<example>/), writes a generator.jsonl side line with the prompt
and response sha256 (never the key, never headers), and turns failures into recovery{generator_fallback} unless
--require-generator is set (then E_GENERATOR).
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from . import USER_AGENT
from .config import RunConfig
from .contracts import Generator, GeneratorResult
from .errors import InvestigatorError

if TYPE_CHECKING:
    from .runner import RunContext

GENERATOR_TEMPERATURE = 0.0
GENERATOR_TIMEOUT_S = 60.0
MIN_SECONDS_LEFT_FOR_GENERATOR = 20


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
                               recorded=True, prompt_sha256=digest, usage=dict(record.get("usage") or {}))


class OpenAIGenerator:
    """Chat completions over urllib. temperature 0, timeout 60 s, prompt/response sha256 logged by the caller."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self._api_key = api_key                  # never logged, never printed
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = f"openai:{model}"
        self.user_agent = USER_AGENT

    def _post(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=data, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": f"Bearer {self._api_key}", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:  # noqa: BLE001 - best effort only
                detail = ""
            raise GeneratorError(f"HTTP {exc.code} from {self.base_url}/chat/completions: {detail}") from None
        except urllib.error.URLError as exc:
            raise GeneratorError(f"connection error: {exc.reason}") from None
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
                               prompt_sha256=prompt_sha256(prompt), usage=usage)


def build_generator(config: RunConfig, example_name: Optional[str] = None) -> Generator:
    """Mount the generator for this run: recorded (sample, when a transcript folder exists) | openai | null."""
    if config.is_sample():
        if example_name:
            folder = config.transcripts_dir / example_name
            if folder.is_dir() and any(folder.glob("*.json")):
                return RecordedGenerator(folder)
        return NullGenerator()
    if config.env.has_generator():
        return OpenAIGenerator(config.env.openai_api_key or "", config.env.openai_base_url, config.env.model)
    return NullGenerator()


def save_transcript(folder: Path, prompt_id: str, prompt: str, result: GeneratorResult, recorded_at: str) -> Path:
    """Write <prompt_id>-<sha12>.json and the prompt text beside it (used by --record-transcripts)."""
    folder.mkdir(parents=True, exist_ok=True)
    name = transcript_filename(prompt_id, prompt)
    record: dict[str, Any] = {"prompt_id": prompt_id, "prompt_sha256": result.prompt_sha256, "model": result.model,
                              "recorded_at": recorded_at, "response": result.text, "usage": result.usage}
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


def generate(ctx: "RunContext", agent: str, prompt_id: str, prompt: str, *, max_tokens: int,
             attempt: int = 1) -> Optional[GeneratorResult]:
    """One bounded generator call for `agent`. None means "use the deterministic fallback" (already logged)."""
    generator = ctx.generator
    if generator is None or generator.name == "null":
        return None
    if ctx.time_left_ms() < MIN_SECONDS_LEFT_FOR_GENERATOR * 1000:
        ctx.recover(agent, "deadline_skip", f"generator pass {prompt_id} skipped: {ctx.time_left_ms() // 1000} s left")
        return None
    if not ctx.budget_take("generator_calls", ctx.budget.max_generator_calls, agent):
        return None
    ctx.counters["generator_attempts"] = ctx.counters.get("generator_attempts", 0) + 1
    try:
        result = generator.complete(prompt_id, prompt, max_tokens=max_tokens, temperature=GENERATOR_TEMPERATURE,
                                    timeout=GENERATOR_TIMEOUT_S)
    except GeneratorError as exc:
        if ctx.config.require_generator:
            raise InvestigatorError("E_GENERATOR", agent, f"generator call {prompt_id} failed: {exc}", retryable=True,
                                    details={"prompt_id": prompt_id, "prompt_sha256": prompt_sha256(prompt)})
        ctx.fallback_used = ctx.fallback_used or ctx.config.mode == "live"
        ctx.recover(agent, "generator_fallback", f"{prompt_id}: {exc}", to="rules/template")
        return None
    if result is None:
        detail = "no recorded transcript" if generator.name == "recorded" else "empty response"
        ctx.recover(agent, "generator_fallback", f"{prompt_id}: {detail}", to="rules/template")
        return None
    response_sha = hashlib.sha256(result.text.encode("utf-8")).hexdigest()
    folder = ctx.run_dir / "transcripts"
    path = save_transcript(folder, prompt_id, prompt, result, ctx.clock.now_iso())
    if ctx.config.record_transcripts and not result.recorded:
        example = ctx.config.example_name or ctx.example_name
        if example:
            save_transcript(ctx.config.transcripts_dir / example, prompt_id, prompt, result, ctx.clock.now_iso())
    line = {"agent": agent, "prompt_id": prompt_id, "attempt": attempt, "generator": generator.name,
            "model": result.model, "recorded": result.recorded, "prompt_sha256": result.prompt_sha256,
            "response_sha256": response_sha, "max_tokens": max_tokens, "temperature": GENERATOR_TEMPERATURE,
            "usage": result.usage, "transcript": str(path.relative_to(ctx.run_dir)).replace("\\", "/")}
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
