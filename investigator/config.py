"""Env + CLI -> RunConfig (SPEC.md sections 4.1 and 4.2).

- load_env_local() reads KEY=VALUE lines from .env.local without printing values (names only are reported).
- resolve_mode() implements the mode rule: `sample` if SAMPLE_MODE=1 or neither OPENAI_API_KEY nor
  ZETARIS_PASSWORD is set; else `live`. `--mode sample` reads no credential and no .env.local; the only
  variable it honours is LOG_DIR (where the run folders go; --out wins over it).
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


@dataclass
class Env:
    """Environment values. Secret fields are repr=False and must never be logged or printed."""

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
    loaded_from_env_local: list[str] = field(default_factory=list)   # variable NAMES only

    def has_generator(self) -> bool:
        return bool(self.openai_api_key)

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


def _flag(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def read_env(environ: Optional[dict[str, str]] = None) -> Env:
    env = dict(os.environ if environ is None else environ)
    get = lambda k, d=None: (env.get(k) or d)  # noqa: E731
    try:
        port = int(get("ARTIFACT_SERVER_PORT", "0") or 0)
    except ValueError:
        port = 0
    return Env(
        openai_api_key=get("OPENAI_API_KEY"),
        openai_base_url=get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        model=get("MODEL", "gpt-4.1-mini"),
        zetaris_base_url=(get("ZETARIS_BASE_URL", "http://localhost:3000") or "").rstrip("/"),
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
    if not env.has_generator() and not env.zetaris_password:
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
    example_name: Optional[str] = None      # transcripts folder under fixtures/transcripts/
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
    def zetaris_results_dir(self) -> Path:
        return self.repo_root / "fixtures" / "zetaris"

    def is_sample(self) -> bool:
        return self.mode == "sample"


def build_config(args: Optional[argparse.Namespace] = None, environ: Optional[dict[str, str]] = None,
                 load_dotenv: bool = True) -> RunConfig:
    """CLI namespace (run.py) + environment -> RunConfig. `--mode sample` reads no credential and no .env.local:
    the only environment variable it honours is LOG_DIR (default logs/), and --out wins over it."""
    ns = args or argparse.Namespace()
    requested: RequestedMode = getattr(ns, "mode", None) or "auto"
    if getattr(ns, "sample", False):
        requested = "sample"
    if requested == "sample":
        log_dir = (os.environ if environ is None else environ).get("LOG_DIR")
        env = Env(sample_mode_flag=True, log_dir=Path(log_dir) if log_dir else REPO_ROOT / "logs")
    else:
        loaded = load_env_local() if (load_dotenv and environ is None) else []
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
        env=env,
    )


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
