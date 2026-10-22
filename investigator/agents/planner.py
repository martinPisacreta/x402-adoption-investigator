"""Planner: question -> Plan (SPEC.md section 6.1). Owner: B4.

Deterministic router first (wallet regex -> payer_dossier; host -> seller_profile; catalog keywords ->
catalog_concentration). When a generator is mounted, one prompts/planner.txt call refines the plan
(hypotheses, extra metrics, and the playbook when the router found more than one candidate); its JSON is
validated by plan.schema.json ($defs/generator_response); invalid -> one retry with the validator message
appended; still invalid -> rules. No match at all -> E_INPUT with details.supported_playbooks. The H-MEM
retrieval (actor planner, threshold 0.35, top 5) fills {{hmem_reinjection}} and Plan.hmem_memory_ids, and is
traced even when no generator is mounted. The playbook decides fetch_list, query_ids and metrics; the `plan`
event lists them before any data moves.

Fetch-list items use the canonical request keys of sources/registry.py (section 7.3) so the FixtureFetcher,
the MANIFEST.requests map and the Collector agree on one key per request. Observation names are the export
names both SQL backends and the reducers read (documented in INTEGRATION_NOTES.md).
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from ..contracts import PLAYBOOKS, AgentResult, Focus, Plan, to_json
from ..errors import InvestigatorError
from ..generator import generate, load_prompt, parse_json_object, render_prompt
from ..schemas import load_schema, validate
from ..sources import registry as registry_mod
from ..sources.registry import SNAPSHOT_KEY, URLS, canonical_request_key, rpc_request_key
from .metrics import METRICS, metrics_for_playbook, query_ids_for_metrics

if TYPE_CHECKING:
    from ..runner import RunContext

WALLET_RE = re.compile(r"0x[0-9a-fA-F]{40}")
HOST_RE = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", re.IGNORECASE)
CATALOG_RE = re.compile(r"concentration|catalog|bazaar|who sells|top hosts|active share", re.IGNORECASE)
ROUTE_QUERY = "x402 adoption"
SAMPLE_PAGE_LIMIT = 100          # the committed p0/p1 fixtures were captured with limit=100
LIVE_PAGE_LIMIT = 1000
PLANNER_MAX_TOKENS = 400
HMEM_THRESHOLD = 0.35
HMEM_TOP_N = 5

PLAYBOOK_QUERIES: dict[str, list[str]] = {
    "seller_profile": [
        "q01_sales_by_wallet_class", "q02_external_payers", "q03_repeat_external_payers", "q04_top_payer_share",
        "q05_external_sales_by_service", "q06_bazaar_host_listings", "q07_bazaar_payto_vs_receipts_seller",
        "q12_registry_count_openapi", "q13_registry_count_agent402", "q14_registry_count_vet402",
        "q15_registry_count_trust_search", "q16_settlements_vs_receipts", "q18_external_sales_by_day",
    ],
    "catalog_concentration": [
        "q08_catalog_size", "q09_catalog_top_hosts_by_calls", "q10_catalog_active_share",
        "q11_catalog_payto_concentration", "q19_trust_ecosystem_stats", "q20_catalog_unique_payers_max",
        "q21_catalog_top_hosts_by_listings",
    ],
    "payer_dossier": ["q16_settlements_vs_receipts", "q17_payer_dossier", "q06_bazaar_host_listings", "q22_payer_chain_facts"],
}

PLAYBOOK_SOURCES: dict[str, list[str]] = {
    "seller_profile": ["receipts", "store_openapi", "cdp_search", "agent402", "x402_trust", "vet402", "base_rpc"],
    "catalog_concentration": ["cdp_catalog_snapshot", "cdp_bazaar", "x402_trust"],
    "payer_dossier": ["receipts", "cdp_search", "base_rpc"],
}

TX_SAMPLE_RULES: dict[str, str] = {
    "seller_profile": "most recent external sale per distinct payer, newest first, capped at max_tx_verifications",
    "catalog_concentration": "none",
    "payer_dossier": "sales of focus.wallet first, then most recent external sale per distinct payer, capped at max_tx_verifications",
}


# ---------------------------------------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------------------------------------

def candidate_playbooks(question: str, focus: Focus) -> list[str]:
    """Every playbook the question/focus could support, in router priority order."""
    found: list[str] = []
    if focus.wallet or WALLET_RE.search(question):
        found.append("payer_dossier")
    if focus.host or HOST_RE.search(question):
        found.append("seller_profile")
    if CATALOG_RE.search(question) or (focus.query and CATALOG_RE.search(focus.query)):
        found.append("catalog_concentration")
    return found


def route_playbook(question: str, focus: Focus) -> Optional[str]:
    """Deterministic router (first match wins): wallet -> payer_dossier; host -> seller_profile; keywords -> catalog."""
    candidates = candidate_playbooks(question, focus)
    return candidates[0] if candidates else None


def plan_params(playbook: str, question: str, focus: Focus) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if focus.host:
        params["host"] = focus.host
    else:
        match = HOST_RE.search(question)
        if match and playbook in ("seller_profile", "payer_dossier"):
            params["host"] = match.group(0).lower()
    if focus.wallet:
        params["wallet"] = focus.wallet
    elif playbook == "payer_dossier":
        match = WALLET_RE.search(question)
        params["wallet"] = match.group(0).lower() if match else None
    if focus.query:
        params["query"] = focus.query
    if focus.service:
        params["service"] = focus.service
    return params


# ---------------------------------------------------------------------------------------------------------
# Fetch list
# ---------------------------------------------------------------------------------------------------------

def _item(name: str, family: str, url: str, *, method: str = "GET", optional: bool = True, priority: int = 5,
          body: Any = None, request_key: Optional[str] = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "name": name, "family": family,
        "request_key": request_key or canonical_request_key(method, url),
        "url": url, "method": method, "optional": optional, "priority": priority,
    }
    if body is not None:
        item["body"] = body
    return item


def _rpc_item(name: str, method: str, params: list[Any], *, priority: int) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return _item(name, "base_rpc", URLS["base_rpc"]["base_rpc"], method="RPC", optional=True, priority=priority,
                 body=body, request_key=rpc_request_key(method, params))


def _url(family: str, key: str, **values: str) -> str:
    from urllib.parse import quote
    template = URLS[family][key]
    return template.format(**{k: quote(v, safe="") if k == "query" else v for k, v in values.items()})


def build_fetch_list(ctx: "RunContext", playbook: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Playbook -> ordered fetch_list items {name, family, request_key, url, method, body?, optional, priority}.

    The registry's default_fetch_list (B1) is the single source of truth for names, keys and mandatory flags;
    the local builder below is the fallback when the registry cannot build a list (e.g. no host for a store)."""
    default = getattr(registry_mod, "default_fetch_list", None)
    if default is not None:
        try:
            items = default(playbook, dict(params), catalog=str(ctx.config.catalog or ctx.inp.options.get("catalog") or "snapshot"),
                            bazaar_pages=ctx.budget.bazaar_pages, fixture_mode=ctx.mounts.fetcher == "fixture")
            return sorted(items, key=lambda it: (int(it.get("priority", 50)), it["name"]))
        except (ValueError, KeyError) as exc:
            ctx.notes.append(f"registry.default_fetch_list unavailable for {playbook}: {exc}; planner built the list")
    return _local_fetch_list(ctx, playbook, params)


def _local_fetch_list(ctx: "RunContext", playbook: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    host = params.get("host")
    items: list[dict[str, Any]] = []
    if playbook in ("seller_profile", "payer_dossier"):
        items.append(_item("store_receipts", "receipts", _url("receipts", "store_receipts"), optional=False, priority=0))
    if playbook == "seller_profile":
        items.append(_item("store_openapi", "store_openapi", _url("store_openapi", "store_openapi"), optional=False, priority=1))
    if playbook in ("seller_profile", "payer_dossier") and host:
        items.append(_item("cdp_search_host", "cdp_search", _url("cdp_search", "cdp_search_host", host=host),
                           optional=(playbook == "payer_dossier"), priority=2))
    if playbook == "seller_profile" and host:
        items.append(_item("agent402_index", "agent402", _url("agent402", "agent402_index", host=host), priority=3))
        items.append(_item("agent402_route", "agent402", _url("agent402", "agent402_route", query=ROUTE_QUERY), priority=4))
        items.append(_item("trust_search", "x402_trust", _url("x402_trust", "trust_search", host=host), priority=5))
        items.append(_item("trust_verified", "x402_trust", _url("x402_trust", "trust_verified"), priority=6))
        items.append(_item("trust_stats", "x402_trust", _url("x402_trust", "trust_stats"), priority=7))
        items.append(_item("vet402_resolve", "vet402", _url("vet402", "vet402_resolve", host=host), priority=8))
    if playbook == "catalog_concentration":
        catalog_mode = ctx.config.catalog or ctx.inp.options.get("catalog") or "snapshot"
        sample = ctx.config.is_sample()
        if catalog_mode == "snapshot" or sample:
            items.append(_item("catalog_snapshot", "cdp_catalog_snapshot",
                               _url("cdp_bazaar", "cdp_page", limit=str(LIVE_PAGE_LIMIT), offset="0"),
                               method="SNAPSHOT", optional=False, priority=0, request_key=SNAPSHOT_KEY))
        limit = SAMPLE_PAGE_LIMIT if sample else LIVE_PAGE_LIMIT
        pages = 2 if sample else max(0, min(ctx.budget.bazaar_pages, 30))
        for page in range(pages):
            items.append(_item(f"cdp_page_{page}", "cdp_bazaar",
                               _url("cdp_bazaar", "cdp_page", limit=str(limit), offset=str(page * limit)),
                               optional=(catalog_mode == "snapshot" or sample), priority=1 + page))
        items.append(_item("trust_stats", "x402_trust", _url("x402_trust", "trust_stats"), priority=10))
        items.append(_item("trust_leaderboard", "x402_trust", _url("x402_trust", "trust_leaderboard"), priority=11))
        items.append(_item("trust_facilitators", "x402_trust", _url("x402_trust", "trust_facilitators"), priority=12))
    if playbook in ("seller_profile", "payer_dossier"):
        # Liveness probe of the Base RPC family; the Verifier runs the settlement batch itself (section 6.4).
        items.append(_rpc_item("rpc_chain_id", "eth_chainId", [], priority=20))
    return sorted(items, key=lambda it: (it["priority"], it["name"]))


# ---------------------------------------------------------------------------------------------------------
# H-MEM retrieval
# ---------------------------------------------------------------------------------------------------------

def retrieve_memories(ctx: "RunContext", question: str, focus: Focus, actor: str = "planner",
                      threshold: float = HMEM_THRESHOLD, top_n: int = HMEM_TOP_N) -> tuple[str, list[str]]:
    """H-MEM query(question + focus text) -> (reinjection block, ids); `memory` event + hmem.trace.jsonl line."""
    text = " ".join(part for part in (question, focus.text()) if part).strip()
    hmem = ctx.hmem
    if hmem is None:
        ctx.log.emit("memory", actor, {"op": "retrieve", "actor": actor, "memory_ids": [],
                                       "details": {"skipped": "H-MEM not mounted", "query_sha256": _sha(text)}})
        return "", []
    try:
        result = hmem.query(text, threshold=threshold, top_n=top_n, actor=actor)
    except NotImplementedError as exc:
        ctx.log.emit("memory", actor, {"op": "retrieve", "actor": actor, "memory_ids": [],
                                       "details": {"skipped": f"H-MEM retrieval not implemented: {exc}"}})
        ctx.notes.append(f"stub: H-MEM retrieval unavailable for {actor} ({exc})")
        return "", []
    ids = list(result.ids())
    hits = [{"memory_id": h.memory.id, "score": round(float(h.score), 4)} for h in result.memories]
    seq = ctx.log.emit("memory", actor, {"op": "retrieve", "actor": actor, "memory_ids": ids,
                                         "details": {"threshold": threshold, "top_n": top_n, "hits": hits,
                                                     "query_sha256": _sha(text)}})
    trace = dict(result.trace or {})
    trace.setdefault("actor", actor)
    trace.setdefault("threshold", threshold)
    trace.setdefault("topN", top_n)
    trace["seq"] = seq
    ctx.log.side("hmem.trace", trace)
    for hit in result.memories:
        ctx.scratch.setdefault("retrievals", []).append({"memory_id": hit.memory.id, "actor": actor,
                                                         "score": round(float(hit.score), 4)})
    return str(result.reinjection or ""), ids


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------------------
# Generator refinement
# ---------------------------------------------------------------------------------------------------------

def refine_with_generator(ctx: "RunContext", question: str, focus: Focus, reinjection: str,
                          playbook: str) -> tuple[Optional[dict[str, Any]], str]:
    """One prompts/planner.txt call (max_tokens 400) -> {playbook, hypotheses, extra_metrics} validated by
    plan.schema.json $defs/generator_response; one retry with the validator message appended; else None."""
    schema = load_schema("plan")
    response_schema = dict(schema["$defs"]["generator_response"])
    response_schema["$defs"] = schema["$defs"]
    metric_ids = ", ".join(metrics_for_playbook(playbook))
    base_prompt = render_prompt(load_prompt("planner"), {
        "question": question, "focus_host": focus.host or "", "focus_wallet": focus.wallet or "",
        "focus_service": focus.service or "", "focus_query": focus.query or "",
        "hmem_reinjection": reinjection or "(no memory retrieved)", "metric_ids": metric_ids})
    prompt = base_prompt
    source = "rules"
    for attempt in (1, 2):
        result = generate(ctx, "planner", "planner", prompt, max_tokens=PLANNER_MAX_TOKENS, attempt=attempt)
        if result is None:
            return None, source
        source = "recorded-generator" if result.recorded else "generator"
        data = parse_json_object(result.text)
        errors = validate(data, response_schema) if data is not None else ["response is not a JSON object"]
        if not errors:
            return data, source
        if attempt == 1:
            ctx.log.emit("retry", "planner", {"target": "planner", "attempt": 2, "max_attempts": 2,
                                              "reason": "generator JSON failed plan.schema.json: " + "; ".join(errors[:3]),
                                              "backoff_ms": 0})
            prompt = base_prompt + "\n\nYour previous reply was invalid: " + "; ".join(errors[:5]) + \
                "\nReturn ONLY the JSON object."
    ctx.recover("planner", "planner_fallback", "generator JSON invalid twice; rules plan kept", to="rules")
    return None, "rules"


# ---------------------------------------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------------------------------------

def run_planner(ctx: "RunContext") -> AgentResult:
    question = ctx.inp.question
    focus = ctx.inp.focus
    candidates = candidate_playbooks(question, focus)
    if not candidates:
        raise InvestigatorError("E_INPUT", "planner",
                                "no playbook matches the question/focus (need a host, a wallet or a catalog keyword)",
                                details={"supported_playbooks": list(PLAYBOOKS)})
    playbook = candidates[0]
    notes: list[str] = []
    reinjection, memory_ids = retrieve_memories(ctx, question, focus)
    hypotheses: list[str] = []
    extra_metrics: list[str] = []
    planner_source = "rules"
    refined, source = refine_with_generator(ctx, question, focus, reinjection, playbook)
    if refined is not None:
        planner_source = source
        proposed = refined.get("playbook")
        if proposed in candidates and proposed != playbook and len(candidates) > 1:
            playbook = proposed            # the generator may only choose among the router's candidates
        elif proposed != playbook:
            notes.append(f"generator proposed {proposed}; router keeps {playbook}")
        hypotheses = [str(h) for h in refined.get("hypotheses", [])][:10]
        known = set(metrics_for_playbook(playbook))
        extra_metrics = [m for m in refined.get("extra_metrics", []) if m in METRICS and m not in known]
    params = plan_params(playbook, question, focus)
    if playbook == "payer_dossier" and not params.get("wallet"):
        raise InvestigatorError("E_INPUT", "planner", "payer_dossier needs a wallet (focus.wallet or 0x... in the question)",
                                details={"supported_playbooks": list(PLAYBOOKS)})
    if playbook == "seller_profile" and not params.get("host"):
        raise InvestigatorError("E_INPUT", "planner", "seller_profile needs a host (focus.host or a host in the question)",
                                details={"supported_playbooks": list(PLAYBOOKS)})
    metrics = metrics_for_playbook(playbook) + [m for m in extra_metrics if METRICS[m].playbooks]
    metrics = metrics[:25]
    query_ids = list(PLAYBOOK_QUERIES[playbook])
    for qid in query_ids_for_metrics(metrics):
        if qid not in query_ids:
            query_ids.append(qid)
    plan = Plan(
        playbook=playbook,  # type: ignore[arg-type]
        params=params,
        fetch_list=build_fetch_list(ctx, playbook, params),
        query_ids=query_ids,
        metrics=metrics,
        tx_sample_rule=TX_SAMPLE_RULES[playbook],
        hypotheses=hypotheses,
        planner_source=planner_source,  # type: ignore[arg-type]
        hmem_memory_ids=memory_ids,
    )
    errors = validate(to_json(plan), load_schema("plan"))
    if errors:
        raise ValueError("Plan does not validate against plan.schema.json: " + "; ".join(errors[:5]))
    ctx.scratch["plan"] = plan
    return AgentResult("planner", "ok", plan, notes=notes)


def plan_to_json(plan: Plan) -> str:
    return json.dumps(to_json(plan), ensure_ascii=False, indent=2)
