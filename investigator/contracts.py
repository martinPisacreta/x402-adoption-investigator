"""Shared contracts for the x402 Adoption Investigator (integrator-owned; SPEC.md section 3).

Every builder imports from this module and never edits it. Field names are wire names: snake_case inside the
investigator JSON (output.json, run.jsonl); the Meterless engines keep camelCase on disk and map in their own
modules. to_json() turns any contract dataclass (recursively) into JSON-ready dicts and lists, preserving the
declared field order so output.json key order is fixed by these declarations.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Protocol

Mode = Literal["sample", "live"]
Status = Literal["ok", "partial", "error"]
AgentStatus = Literal["ok", "degraded", "failed"]

HARD_CAP_SECONDS = 600
PLAYBOOKS: tuple[str, ...] = ("seller_profile", "catalog_concentration", "payer_dossier")
SOURCE_FAMILIES: tuple[str, ...] = (
    "receipts", "cdp_bazaar", "cdp_search", "cdp_catalog_snapshot", "store_openapi",
    "agent402", "x402_trust", "vet402", "base_rpc",
)
VERDICT_LABELS: tuple[str, ...] = (
    "adoption_real", "adoption_thin", "self_settled_mostly", "concentrated", "diffuse", "insufficient_evidence",
)


@dataclass(frozen=True)
class Budget:
    max_seconds: int = 180          # hard cap 600
    max_fetches: int = 40
    max_sql: int = 60
    max_generator_calls: int = 12
    max_critic_rounds: int = 2
    max_tx_verifications: int = 5
    bazaar_pages: int = 3           # live pages of limit=1000

    def clamped(self) -> "Budget":
        """Return a copy with max_seconds capped at HARD_CAP_SECONDS."""
        return dataclasses.replace(self, max_seconds=max(1, min(self.max_seconds, HARD_CAP_SECONDS)))


@dataclass
class Focus:
    host: Optional[str] = None      # lowercased
    wallet: Optional[str] = None    # 0x + 40 hex, lowercased
    service: Optional[str] = None
    query: Optional[str] = None

    def text(self) -> str:
        return " ".join(v for v in (self.host, self.wallet, self.service, self.query) if v)


@dataclass
class RunInput:
    question: str
    focus: Focus
    budget: Budget
    options: dict[str, Any]         # catalog: "pages"|"snapshot"; zetaris_source: "captured"|"direct"


@dataclass
class Mounts:
    mode: Mode
    fetcher: Literal["live", "fixture", "live+fixture-fallback"]
    executor: Literal["zetaris", "sqlite"]
    generator: str                  # "nightshift:<routing mode>" | "openai:<model>" | "recorded" | "null"
    fixtures_captured_at: Optional[str]   # MANIFEST.fetchedAt when any fixture was used
    zetaris_version: Optional[str]  # "2.4.3.1" when live
    zetaris_source: Optional[Literal["captured", "direct"]]


@dataclass
class Evidence:
    kind: Literal["url", "tx", "log_line"]
    ref: str                        # https://..., https://basescan.org/tx/0x..., log://<run_id>#<seq>
    sha256: Optional[str] = None    # bytes digest for url evidence
    captured_at: Optional[str] = None
    replayed: bool = False
    stale: bool = False
    http: Optional[int] = None
    verified: Optional[bool] = None  # tx evidence only
    fixture_file: Optional[str] = None


@dataclass
class Coverage:
    items_seen: int
    total_reported: Optional[int]
    share: Optional[float]
    basis: str                      # "live pages (3 x 1000)" | "compact snapshot 2026-10-22" | "search capped at 20" | "full source"


@dataclass
class Derivation:
    agent: str                      # "analyst" | "verifier"
    query_id: Optional[str]         # "q04_top_payer_share"
    sql_sha256: Optional[str]
    backend: Optional[str]          # "zetaris" | "sqlite" | "sqlite-fallback"
    reducer: Optional[str]          # "reducers.top_payer_share"
    rows: int = 0
    fields: list[str] = field(default_factory=list)    # source fields touched, e.g. ["l30DaysUniquePayers"]
    sources: list[str] = field(default_factory=list)   # source families touched, e.g. ["receipts", "cdp_search"]
    log_seq: Optional[int] = None   # seq of the sql/verify event


@dataclass
class Critique:
    round: int
    rule: str                       # "C1".."C7", "B", "LLM"
    verdict: Literal["accept", "reject", "revise"]
    reason: str
    memory_ids: list[str] = field(default_factory=list)


@dataclass
class Claim:
    id: str                         # "c01"
    metric: str
    subject: str                    # "seller:host:tienda.textokit.store" | "catalog:cdp-bazaar" | "wallet:base:0x..."
    statement: str                  # templated from the registry
    value: Any                      # number | {"numerator": n, "denominator": d, "share": f} | str
    unit: str                       # "count" | "atomic_usdc" | "share" | "iso8601" | "text"
    period: Optional[str] = None    # "all_time" | "l30d" | "2026-10-20..2026-10-21"
    evidence: list[Evidence] = field(default_factory=list)
    derivation: Optional[Derivation] = None
    coverage: Optional[Coverage] = None
    caveats: list[str] = field(default_factory=list)     # codes; each has a memory_id when it comes from H-MEM
    caveat_memory_ids: dict[str, str] = field(default_factory=dict)
    conflict_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: Literal["proposed", "accepted", "rejected", "revised"] = "proposed"
    cross_checked: bool = False
    critique: list[Critique] = field(default_factory=list)


@dataclass
class ConflictValue:
    source: str                     # family or registry name
    value: Any
    url: str
    basis: str                      # "30-day window" | "all-time" | "per endpoint" | "search capped at 20" | ...
    captured_at: Optional[str] = None


@dataclass
class Conflict:
    id: str                         # "k01"
    predicate: str                  # "endpoint_count_for_host"
    subject: str
    values: list[ConflictValue]
    explanation: str                # why they differ (measurement basis); rule-written
    status: Literal["open", "explained", "resolved"]
    claim_ids: list[str] = field(default_factory=list)


@dataclass
class Observation:
    name: str                       # "store_receipts", "cdp_search_host", "cdp_page_0", "rpc_receipt_<hash>"
    family: str                     # one of SOURCE_FAMILIES
    request_key: str                # canonical key (SPEC.md section 7.3)
    url: str
    method: str
    http: Optional[int]
    bytes_sha256: Optional[str]
    latency_ms: int
    captured_at: str
    replayed: bool
    stale: bool
    status: Literal["ok", "unavailable", "fixture_miss"]
    error: Optional[str]
    payload: Any                    # parsed JSON (or text)
    fixture_file: Optional[str] = None
    export_path: Optional[str] = None   # logs/<run_id>/exports/<name>.json


@dataclass
class Verification:
    tx: str
    status: Literal["verified", "failed", "unverifiable"]
    payer: Optional[str]
    seller: Optional[str]
    amount_atomic: Optional[int]
    block: Optional[int]
    block_time: Optional[str]
    facilitator_from: Optional[str]
    basescan_url: str
    decoded_from: str               # "Transfer log" | "AuthorizationUsed log" | "input transferWithAuthorization"
    receipt_match: Optional[bool]
    reason: Optional[str] = None    # "fixture_miss" | "rpc_unavailable" | "decode_mismatch:<field>"


@dataclass
class Plan:
    playbook: Literal["seller_profile", "catalog_concentration", "payer_dossier"]
    params: dict[str, Any]
    fetch_list: list[dict]          # [{name, family, request_key, url, method, body?, optional: bool, priority: int}]
    query_ids: list[str]
    metrics: list[str]
    tx_sample_rule: str
    hypotheses: list[str]
    planner_source: Literal["rules", "generator", "recorded-generator"]
    hmem_memory_ids: list[str]

    def source_families(self) -> list[str]:
        seen: list[str] = []
        for item in self.fetch_list:
            fam = item.get("family")
            if fam and fam not in seen:
                seen.append(fam)
        return seen


@dataclass
class AgentResult:
    agent: str
    status: AgentStatus
    payload: Any
    notes: list[str] = field(default_factory=list)


class Fetcher(Protocol):
    mode: str

    def fetch(self, request_key: str, url: str, method: str = "GET", body: Any = None,
              timeout: float = 10.0) -> Observation: ...


@dataclass
class GeneratorResult:
    text: str
    model: str                      # the model that answered (response "model" field, else the requested one)
    recorded: bool
    prompt_sha256: str
    usage: dict[str, int]
    provider: str = ""              # "meterless" (Nightshift) | "openai" | the recorded call's provider
    requested_model: Optional[str] = None   # what was asked for, e.g. the routing mode "meterless/auto"
    routing: dict[str, Any] = field(default_factory=dict)   # Nightshift compute-receipt facts (generator.meterless_receipt)


class Generator(Protocol):
    name: str

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]: ...


@dataclass
class SqlResult:
    query_id: str
    executor: str
    headers: list[str]
    rows: list[list[Any]]           # typed per .meta.json
    rowcount: int
    time_used_ms: int
    sql_sha256: str
    fallback_reason: Optional[str] = None
    zetaris_version: Optional[str] = None
    tables: list[dict] = field(default_factory=list)


class SqlExecutor(Protocol):
    name: str                       # "zetaris" | "sqlite"

    def ensure_views(self, exports: dict[str, str]) -> list[dict]: ...   # returns tables:[{name, source_url}]

    def execute(self, query_id: str, sql: str, params: dict[str, Any], meta: dict) -> SqlResult: ...

    def cleanup(self) -> None: ...


def to_json(obj: Any) -> Any:
    """Recursively convert contract dataclasses to JSON-ready values (field order preserved)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj
