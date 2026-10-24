"""Env + CLI -> RunConfig (SPEC.md sections 4.1 and 4.2).

- load_env_local() reads KEY=VALUE lines from one env file without printing values (names only are reported);
  load_env_files() reads .env.local and then .env, so a name .env.local sets wins over .env (and the shell over both).
- ZETARIS_REST_URL (the name the hackathon guides suggest) is an alias of ZETARIS_BASE_URL, which wins when both are set.
- resolve_mode() implements the mode rule: `sample` if SAMPLE_MODE=1 or none of OPENAI_API_KEY,
  ZETARIS_PASSWORD and the Nightshift variables (NIGHTSHIFT_BASE_URL / METERLESS_BASE_URL, NIGHTSHIFT_TOKEN /
  METERLESS_RUNTIME_TOKEN / METERLESS_TOKEN, NIGHTSHIFT_TOKEN_FILE) is set; else `live`. Mode resolution never probes
  the network (Nightshift Auto Detect runs only once the mode is live, in generator.build_generator), and the
  runtime's own <stateDir>/proxy-token file never makes a run live by itself. `--mode sample` reads no credential
  and no env file (.env.local, .env); the only variable it honours is LOG_DIR (where the run folders go; --out wins over it).
- Nightshift aliases: the hackathon guides name METERLESS_BASE_URL, METERLESS_RUNTIME_TOKEN and METERLESS_MODEL, the
  official `ns` CLI METERLESS_TOKEN and METERLESS_STATE_DIR; each NIGHTSHIFT_* name wins over its aliases.
  NIGHTSHIFT_REASONING_EFFORT / METERLESS_REASONING_EFFORT (default `none`) is the thinking switch of every Nightshift
  chat request (generator.nightshift_call_options): `none` turns a reasoning model's thinking off, `default` sends
  nothing, minimal | low | medium | high | xhigh are passed on as `reasoning_effort`.
  meterless_state_dir() copies the CLI's stateDir rule (METERLESS_STATE_DIR, else %LOCALAPPDATA%/ai.meterless.runtime
  on Windows, ~/Library/Application Support/ai.meterless.runtime on macOS, /var/lib/meterless elsewhere), read
  from the environment mapping it is given so tests stay hermetic.
- parse_request() validates the run.py input against schemas/input.schema.json and builds RunInput; a bad
  input is E_INPUT with details.
Secrets live only in the Env dataclass fields marked repr=False and are never logged.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

from .contracts import Budget, Focus, HARD_CAP_SECONDS, Mode, RunInput
from .errors import InvestigatorError
from .schemas import load_schema, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
RequestedMode = Literal["sample", "live", "auto"]
NIGHTSHIFT_DEFAULT_MODEL = "meterless/auto"     # Meterless routing mode (not a model file); NIGHTSHIFT_MODEL overrides
# (name, aliases...): the first one set wins; the aliases are the names of the hackathon guides and the ns CLI
NIGHTSHIFT_BASE_URL_VARS: tuple[str, ...] = ("NIGHTSHIFT_BASE_URL", "METERLESS_BASE_URL")
NIGHTSHIFT_TOKEN_VARS: tuple[str, ...] = ("NIGHTSHIFT_TOKEN", "METERLESS_RUNTIME_TOKEN", "METERLESS_TOKEN")
NIGHTSHIFT_MODEL_VARS: tuple[str, ...] = ("NIGHTSHIFT_MODEL", "METERLESS_MODEL")
NIGHTSHIFT_REASONING_VARS: tuple[str, ...] = ("NIGHTSHIFT_REASONING_EFFORT", "METERLESS_REASONING_EFFORT")
ZETARIS_BASE_URL_VARS: tuple[str, ...] = ("ZETARIS_BASE_URL", "ZETARIS_REST_URL")
# Env files read outside sample mode, in this order; a name set by an earlier file (or the shell) is not overwritten.
ENV_FILES: tuple[str, ...] = (".env.local", ".env")
# Thinking off by default: the three generator-backed roles answer in JSON or short prose within 400-700 tokens, and
# with thinking on both Nemotron routes spent the Critic's 500-token budget thinking and answered nothing (HTTP 502
# empty_response, 2026-10-24, examples/live-trace-nightshift/nemotron-*/smoke/). "default" sends no thinking control.
NIGHTSHIFT_DEFAULT_REASONING_EFFORT = "none"
NIGHTSHIFT_REASONING_EFFORTS: tuple[str, ...] = ("none", "minimal", "low", "medium", "high", "xhigh", "default")
METERLESS_LINUX_STATE_DIR = "/var/lib/meterless"   # the ns CLI's DEFAULT_STATE_DIR (Linux / packaged DGX daemon)


@dataclass
class Env:
    """Environment values. Secret fields are repr=False and must never be logged or printed."""

    # Meterless Nightshift: the primary generator (OpenAI-compatible Meterless Runtime, provider id `meterless`)
    nightshift_base_url: Optional[str] = None       # NIGHTSHIFT_BASE_URL | METERLESS_BASE_URL as pasted; normalised to <host>/v1
    nightshift_base_url_var: str = "NIGHTSHIFT_BASE_URL"   # which of the two names set it (a name, never a value)
    nightshift_token: Optional[str] = field(default=None, repr=False)   # NIGHTSHIFT_TOKEN | METERLESS_RUNTIME_TOKEN | METERLESS_TOKEN
    nightshift_token_var: str = "NIGHTSHIFT_TOKEN"  # which name carried the token (only the name is ever logged)
    nightshift_token_file: Optional[str] = None     # NIGHTSHIFT_TOKEN_FILE: path of <stateDir>/proxy-token
    nightshift_state_dir: Optional[str] = None      # the runtime's stateDir (METERLESS_STATE_DIR or the OS default)
    nightshift_state_dir_var: str = ""              # "METERLESS_STATE_DIR" | "default" | "" (none known)
    nightshift_model: str = NIGHTSHIFT_DEFAULT_MODEL
    nightshift_reasoning_effort: str = NIGHTSHIFT_DEFAULT_REASONING_EFFORT   # NIGHTSHIFT_REASONING_EFFORT | METERLESS_REASONING_EFFORT
    nightshift_autodetect: bool = True              # NIGHTSHIFT_AUTODETECT=0: no localhost probe without a base URL
    # OpenAI-compatible fallback generator
    openai_api_key: Optional[str] = field(default=None, repr=False)
    openai_base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    zetaris_base_url: str = "http://localhost:3000"
    zetaris_username: Optional[str] = field(default=None, repr=False)
    zetaris_password: Optional[str] = field(default=None, repr=False)
    zetaris_org_id: Optional[str] = None
    zetaris_api_key: Optional[str] = field(default=None, repr=False)
    zetaris_api_style: str = "proxy"
    artifact_server_host: str = "host.docker.internal"
    artifact_server_port: int = 0
    sample_mode_flag: bool = False
    hmem_dir: Path = REPO_ROOT / "memory" / "state"
    log_dir: Path = REPO_ROOT / "logs"
    loaded_from_env_local: list[str] = field(default_factory=list)   # variable NAMES only (.env.local, then .env)

    def has_generator(self) -> bool:
        """An OpenAI-compatible key is configured (the fallback generator)."""
        return bool(self.openai_api_key)

    def has_nightshift_config(self) -> bool:
        """A Nightshift variable is set (Auto Detect may still find a runtime without any). The stateDir's
        proxy-token alone does not count: an installed runtime never switches `--mode auto` to live."""
        return bool(self.nightshift_base_url or self.nightshift_token or self.nightshift_token_file)

    def has_zetaris(self) -> bool:
        return bool(self.zetaris_password or self.zetaris_api_key)


def load_env_local(path: Optional[Path] = None, override: bool = False) -> list[str]:
    """Load KEY=VALUE lines into os.environ (existing variables win unless override). Returns the names."""
    env_path = Path(path) if path else REPO_ROOT / ".env.local"
    names: list[str] = []
    if not env_path.is_file():
        return names
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or (key in os.environ and not override):
            continue
        os.environ[key] = value
        names.append(key)
    return names


def load_env_files(root: Optional[Path] = None) -> list[str]:
    """Load ENV_FILES from `root` (the repository root by default): .env.local first, then .env. Existing variables
    win, so the shell beats .env.local and .env.local beats .env. Returns the names loaded (never the values)."""
    base = Path(root) if root else REPO_ROOT
    names: list[str] = []
    for name in ENV_FILES:
        names += load_env_local(base / name)
    return names


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def first_set(env: dict[str, str], names: tuple[str, ...]) -> tuple[Optional[str], str]:
    """(value, name) of the first variable in `names` with a non-blank value; (None, names[0]) when none is."""
    for name in names:
        value = env.get(name)
        if value and value.strip():
            return value, name
    return None, names[0]


def meterless_state_dir(env: dict[str, str], platform: Optional[str] = None) -> tuple[Optional[str], str]:
    """(stateDir, "METERLESS_STATE_DIR" | "default" | "") by the official ns CLI's rule (lib/transport.mjs stateDir):
    METERLESS_STATE_DIR, else %LOCALAPPDATA%/ai.meterless.runtime on Windows (USERPROFILE/AppData/Local when
    LOCALAPPDATA is unset), ~/Library/Application Support/ai.meterless.runtime on macOS, /var/lib/meterless
    elsewhere. Only `env` is read (never os.environ behind the caller's back); a Windows or macOS default whose base
    variable is missing gives (None, "")."""
    explicit = (env.get("METERLESS_STATE_DIR") or "").strip()
    if explicit:
        return explicit, "METERLESS_STATE_DIR"
    platform = platform or sys.platform
    if platform.startswith("win"):
        local = (env.get("LOCALAPPDATA") or "").strip()
        if not local and (env.get("USERPROFILE") or "").strip():
            local = str(Path(env["USERPROFILE"].strip()) / "AppData" / "Local")
        return (str(Path(local) / "ai.meterless.runtime"), "default") if local else (None, "")
    if platform == "darwin":
        home = (env.get("HOME") or "").strip()
        if not home:
            return None, ""
        return str(Path(home) / "Library" / "Application Support" / "ai.meterless.runtime"), "default"
    return METERLESS_LINUX_STATE_DIR, "default"


def read_env(environ: Optional[dict[str, str]] = None) -> Env:
    env = dict(os.environ if environ is None else environ)
    get = lambda k, d=None: (env.get(k) or d)  # noqa: E731
    try:
        port = int(get("ARTIFACT_SERVER_PORT", "0") or 0)
    except ValueError:
        port = 0
    base_url, base_url_var = first_set(env, NIGHTSHIFT_BASE_URL_VARS)
    token, token_var = first_set(env, NIGHTSHIFT_TOKEN_VARS)
    routing_mode, _ = first_set(env, NIGHTSHIFT_MODEL_VARS)
    reasoning_effort, _ = first_set(env, NIGHTSHIFT_REASONING_VARS)
    state_dir, state_dir_var = meterless_state_dir(env)
    zetaris_url, _ = first_set(env, ZETARIS_BASE_URL_VARS)
    return Env(
        nightshift_base_url=base_url,
        nightshift_base_url_var=base_url_var,
        nightshift_token=token,
        nightshift_token_var=token_var,
        nightshift_token_file=get("NIGHTSHIFT_TOKEN_FILE"),
        nightshift_state_dir=state_dir,
        nightshift_state_dir_var=state_dir_var,
        nightshift_model=(routing_mode or NIGHTSHIFT_DEFAULT_MODEL).strip(),
        nightshift_reasoning_effort=(reasoning_effort or NIGHTSHIFT_DEFAULT_REASONING_EFFORT).strip().lower(),
        nightshift_autodetect=_flag(get("NIGHTSHIFT_AUTODETECT", "1")),
        openai_api_key=get("OPENAI_API_KEY"),
        openai_base_url=get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=get("MODEL", "gpt-4.1-mini"),
        zetaris_base_url=(zetaris_url or "http://localhost:3000").strip().rstrip("/"),
        zetaris_username=get("ZETARIS_USERNAME"),
        zetaris_password=get("ZETARIS_PASSWORD"),
        zetaris_org_id=get("ZETARIS_ORG_ID"),
        zetaris_api_key=get("ZETARIS_API_KEY"),
        zetaris_api_style=get("ZETARIS_API_STYLE", "proxy"),
        artifact_server_host=get("ARTIFACT_SERVER_HOST", "host.docker.internal"),
        artifact_server_port=port,
        sample_mode_flag=_flag(get("SAMPLE_MODE")),
        hmem_dir=Path(get("HMEM_DIR", str(REPO_ROOT / "memory" / "state"))),
        log_dir=Path(get("LOG_DIR", str(REPO_ROOT / "logs"))),
    )


def resolve_mode(requested: RequestedMode, env: Env) -> Mode:
    if requested == "sample":
        return "sample"
    if requested == "live":
        return "live"
    if env.sample_mode_flag:
        return "sample"
    if not env.has_generator() and not env.zetaris_password and not env.has_nightshift_config():
        return "sample"
    return "live"


@dataclass
class RunConfig:
    requested_mode: RequestedMode = "auto"
    mode: Mode = "sample"
    require_zetaris: bool = False
    require_generator: bool = False
    zetaris_source: Optional[Literal["captured", "direct"]] = None   # CLI override of input.options
    catalog: Optional[Literal["pages", "snapshot"]] = None           # CLI override of input.options
    out_dir: Path = REPO_ROOT / "logs"
    output_path: Optional[Path] = None      # --output: extra copy of the final JSON envelope
    record_transcripts: bool = False
    print_output: bool = False
    quiet: bool = False
    verbose: bool = False
    keep_zetaris_objects: bool = False
    hmem_sleep: bool = False
    example_name: Optional[str] = None      # fixtures folder name: transcripts/<name>/ and transient/<name>/
    fetch_retries: int = 2                  # retries per source request (sources/retry.py); --fetch-retries, 0..5
    env: Env = field(default_factory=Env)
    repo_root: Path = REPO_ROOT
    seed_dir_override: Optional[Path] = None   # scripts/memory_ablation.py: run with a different seed (same mounts)

    @property
    def fixtures_dir(self) -> Path:
        return self.repo_root / "fixtures" / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.fixtures_dir / "MANIFEST.json"

    @property
    def seed_dir(self) -> Path:
        return self.seed_dir_override or (self.repo_root / "memory" / "seed")

    @property
    def sql_dir(self) -> Path:
        return self.repo_root / "investigator" / "sql"

    @property
    def transcripts_dir(self) -> Path:
        return self.repo_root / "fixtures" / "transcripts"

    @property
    def transient_dir(self) -> Path:
        """fixtures/transient/<example>/faults.json: transient failures replayed in sample mode (sources/transient.py)."""
        return self.repo_root / "fixtures" / "transient"

    @property
    def zetaris_results_dir(self) -> Path:
        return self.repo_root / "fixtures" / "zetaris"

    def is_sample(self) -> bool:
        return self.mode == "sample"


def build_config(args: Optional[argparse.Namespace] = None, environ: Optional[dict[str, str]] = None,
                 load_dotenv: bool = True) -> RunConfig:
    """CLI namespace (run.py) + environment -> RunConfig. `--mode sample` reads no credential and no env file:
    the only environment variable it honours is LOG_DIR (default logs/), and --out wins over it."""
    ns = args or argparse.Namespace()
    requested: RequestedMode = getattr(ns, "mode", None) or "auto"
    if getattr(ns, "sample", False):
        requested = "sample"
    if requested == "sample":
        log_dir = (os.environ if environ is None else environ).get("LOG_DIR")
        env = Env(sample_mode_flag=True, log_dir=Path(log_dir) if log_dir else REPO_ROOT / "logs")
    else:
        loaded = load_env_files() if (load_dotenv and environ is None) else []
        env = read_env(environ)
        env.loaded_from_env_local = loaded
    mode = resolve_mode(requested, env)
    out_dir = Path(getattr(ns, "out", None) or env.log_dir)
    output_path = getattr(ns, "output", None)
    return RunConfig(
        requested_mode=requested,
        mode=mode,
        require_zetaris=bool(getattr(ns, "require_zetaris", False)),
        require_generator=bool(getattr(ns, "require_generator", False)),
        zetaris_source=getattr(ns, "zetaris_source", None),
        catalog=getattr(ns, "catalog", None),
        out_dir=out_dir,
        output_path=Path(output_path) if output_path else None,
        record_transcripts=bool(getattr(ns, "record_transcripts", False)),
        print_output=bool(getattr(ns, "print_output", False)),
        quiet=bool(getattr(ns, "quiet", False)),
        verbose=bool(getattr(ns, "verbose", False)),
        keep_zetaris_objects=bool(getattr(ns, "keep_zetaris_objects", False)),
        hmem_sleep=bool(getattr(ns, "hmem_sleep", False)),
        example_name=getattr(ns, "example", None),
        fetch_retries=fetch_retries_from(getattr(ns, "fetch_retries", None)),
        env=env,
    )


def fetch_retries_from(value: Any) -> int:
    """--fetch-retries N -> 0..5 (default 2, the RetryPolicy default)."""
    from .sources.retry import RetryPolicy
    return RetryPolicy.from_retries(None if value is None else int(value)).max_retries


def default_run_id(now: Optional[_dt.datetime] = None) -> str:
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{secrets.token_hex(2)}"


def budget_from(data: Optional[dict[str, Any]]) -> Budget:
    data = dict(data or {})
    base = Budget()
    kwargs: dict[str, Any] = {}
    for name in ("max_seconds", "max_fetches", "max_sql", "max_generator_calls", "max_critic_rounds",
                 "max_tx_verifications", "bazaar_pages"):
        if name in data and data[name] is not None:
            kwargs[name] = int(data[name])
    budget = Budget(**{**base.__dict__, **kwargs})
    return budget.clamped()


def parse_request(payload: Any, run_id_override: Optional[str] = None) -> tuple[str, RunInput]:
    """Validate {run_id?, input} and build RunInput. Raises InvestigatorError(E_INPUT)."""
    if not isinstance(payload, dict):
        raise InvestigatorError("E_INPUT", "input", "request body must be a JSON object with an input field",
                                details={"schema": "investigator/schemas/input.schema.json"})
    errors = validate(payload, load_schema("input"))
    if errors:
        raise InvestigatorError("E_INPUT", "input", "input does not match input.schema.json",
                                details={"errors": errors[:20], "schema": "investigator/schemas/input.schema.json"})
    run_id = run_id_override or payload.get("run_id") or default_run_id()
    if not RUN_ID_RE.match(run_id) or run_id.strip(".") == "":
        raise InvestigatorError("E_INPUT", "input", "run_id must match ^[A-Za-z0-9._-]{1,64}$ and not be only dots",
                                details={"run_id": run_id})
    data = payload["input"]
    focus_raw = data.get("focus") or {}
    wallet = focus_raw.get("wallet")
    if wallet is not None and not WALLET_RE.match(wallet):
        raise InvestigatorError("E_INPUT", "input", "focus.wallet must be 0x followed by 40 hex characters",
                                details={"wallet": wallet})
    focus = Focus(
        host=(focus_raw.get("host") or None) and focus_raw["host"].strip().lower(),
        wallet=wallet.lower() if wallet else None,
        service=focus_raw.get("service") or None,
        query=focus_raw.get("query") or None,
    )
    options = dict(data.get("options") or {})
    options.setdefault("catalog", "snapshot")
    options.setdefault("zetaris_source", "captured")
    run_input = RunInput(question=data["question"].strip(), focus=focus,
                         budget=budget_from(data.get("budget")), options=options)
    return run_id, run_input


def max_seconds_cap() -> int:
    return HARD_CAP_SECONDS
