"""Analyst: metric registry -> SQL via the executor + Python reducer -> Claims, Conflicts, unavailable (SPEC.md 6.5). Owner: B4.

For every metric in Plan.metrics: run the committed SQL through ctx.executor (sql event + sql.jsonl line), run
the reducer over the run exports, compare (counts/sums exact, shares within 1e-6). Agree -> cross_checked true
and the registry confidence; disagree -> an open Conflict <metric>:backend_disagreement, confidence 0.5, and the
Critic's C2 rejects the claim; mounted executor unusable -> the same SQL on an embedded SQLite executor over the
same exports (recovery zetaris_query_fallback); a claim still left with one value (query skipped or over budget)
is proposed as computed and rejected by C2. Required source unavailable -> unavailable[], no claim (never a zero). Conflicts: endpoint_count_for_host (q06, q12-q15), calls_30d_for_host,
ecosystem_size, backend disagreement, with rule-written explanations from metrics.CONFLICT_EXPLANATIONS.
Bounds: <= 25 metrics, each query <= 30 s, total SQL <= 60 s, budget.max_sql statements, deadline checks.

The Analyst is tool-only: no generator is ever called here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..contracts import (AgentResult, Claim, Conflict, ConflictValue, Coverage, Derivation, Evidence, Observation,
                         Plan, SqlResult, Verification)
from ..sources.catalog import SNAPSHOT_BASIS
from ..zetaris.catalog import QuerySpec, load_catalog
from ..zetaris.executor import backend_label
from . import collector as collector_mod
from . import reducers
from .metrics import (CONFLICT_EXPLANATIONS, MAX_METRICS_PER_RUN, METRICS, REGISTRY_BASIS, SQL_QUERY_TIMEOUT_S,
                      SQL_TOTAL_TIMEOUT_S, MetricSpec)
from .pitfalls import required_caveats
from .reducers import WorldExports, format_pairs, get_extractor, get_reducer, window_since

if TYPE_CHECKING:
    from ..runner import RunContext

SHARE_TOLERANCE = 1e-6
CONFIDENCE_NOT_CROSS_CHECKED = 0.7
CONFIDENCE_BACKEND_DISAGREEMENT = 0.5
WORLD_EXPORT_FILES = ("world-entities", "world-relationships", "world-facts")


def claim_id(index: int) -> str:
    return f"c{index:02d}"


def conflict_id(index: int) -> str:
    return f"k{index:02d}"


def values_agree(sql_value: Any, reducer_value: Any, unit: str) -> bool:
    """Exact for counts/sums/text; within 1e-6 for shares (dict with share or bare float)."""
    if unit == "share":
        a = sql_value.get("share") if isinstance(sql_value, dict) else sql_value
        b = reducer_value.get("share") if isinstance(reducer_value, dict) else reducer_value
        if a is None and b is None:
            return _strip_share(sql_value) == _strip_share(reducer_value)
        if a is None or b is None:
            return False
        return abs(float(a) - float(b)) <= SHARE_TOLERANCE and _strip_share(sql_value) == _strip_share(reducer_value)
    return sql_value == reducer_value


def _strip_share(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "share"}
    return value


# ---------------------------------------------------------------------------------------------------------
# Formatting helpers shared with the Writer/Critic (one place so the narrative tokens map back to values)
# ---------------------------------------------------------------------------------------------------------

def format_usdc(atomic: Any) -> str:
    """Atomic USDC (6 decimals) -> decimal string without trailing zeros (1000 atomic -> "0.001")."""
    try:
        value = int(atomic)
    except (TypeError, ValueError):
        return str(atomic)
    whole, frac = divmod(abs(value), 1_000_000)
    text = f"{whole}.{frac:06d}".rstrip("0").rstrip(".")
    return ("-" if value < 0 else "") + (text or "0")


def format_share_pct(share: Any) -> str:
    try:
        return f"{float(share) * 100:.1f}"
    except (TypeError, ValueError):
        return "n/a"


def format_value(value: Any) -> str:
    if isinstance(value, dict):
        if "share" in value:
            return f"{value.get('numerator')} of {value.get('denominator')}"
        return format_pairs(list(value.items()))
    if isinstance(value, list):
        return ", ".join(format_value(v) for v in value)
    return str(value)


def render_statement(spec: MetricSpec, value: Any, params: dict[str, Any], until: str) -> str:
    slots: dict[str, Any] = {k: v for k, v in params.items() if v is not None}
    slots.update({"value": format_value(value), "until": until})
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in ("value",) and not isinstance(item, (dict, list)):
                slots.setdefault(key, item)
        if "share" in value:
            slots["share_pct"] = format_share_pct(value.get("share"))
    if spec.unit == "atomic_usdc":
        slots["usdc"] = format_usdc(value)
    try:
        return spec.statement_template.format(**slots)
    except (KeyError, IndexError):
        return spec.statement_template.replace("{value}", slots["value"])


# ---------------------------------------------------------------------------------------------------------
# Observations, exports and the WorldExports view
# ---------------------------------------------------------------------------------------------------------

def name_observations(plan: Plan, observations: list[Observation]) -> dict[str, Observation]:
    """Observation by export name: the plan item name matched by request_key, else the observation's own name."""
    by_key = {item["request_key"]: item["name"] for item in plan.fetch_list}
    named: dict[str, Observation] = {}
    for obs in observations:
        name = by_key.get(obs.request_key, obs.name)
        named.setdefault(name, obs)
    return named


def families_ok(observations: list[Observation]) -> dict[str, list[Observation]]:
    ok: dict[str, list[Observation]] = {}
    for obs in observations:
        if obs.status == "ok":
            ok.setdefault(obs.family, []).append(obs)
    return ok


def chain_reached(world: WorldExports, verifications: list[Verification]) -> bool:
    """The base_rpc family is available when the Verifier reached the chain: a settlements export, chain facts or
    at least one verification that was actually decoded (the Collector never fetches the RPC batch itself)."""
    if world.raw.get("settlements") is not None or world.chain_facts:
        return True
    return any(v.status in ("verified", "failed") for v in verifications)


def family_failure_reason(family: str, observations: list[Observation]) -> str:
    failed = [o for o in observations if o.family == family]
    if family == "base_rpc" and not failed:
        return "base_rpc unavailable: the Verifier produced no settlements export, no chain facts and no decoded verification (RPC not queried)"
    if not failed:
        return f"{family} not fetched (not in the plan or skipped)"
    obs = failed[0]
    return f"{family} {obs.status}: {obs.error or 'no payload'}"


def _export_file(ctx: "RunContext", obs: Observation, name: str) -> Path:
    if obs.export_path:
        resolver = getattr(collector_mod, "absolute_path", None)
        return resolver(ctx, obs.export_path) if resolver else Path(obs.export_path)
    return ctx.exports_dir / f"{name}.json"


def build_exports(ctx: "RunContext", named: dict[str, Observation]) -> dict[str, str]:
    """{export name: absolute file path} for the executor: the Collector's exports (through collector.exports_map
    when available; written here only when a file is missing), the Verifier's settlements.json, the Collector's
    openapi_paid_ops.json, the Verifier's settlements.json and chain_facts.json and the Keeper's world-*.json.
    Everything lives in logs/<run_id>/exports/."""
    exports: dict[str, str] = {}
    mapper = getattr(collector_mod, "exports_map", None)
    if mapper is not None:
        try:
            exports.update({str(k): str(v) for k, v in mapper(ctx, list(named.values())).items()})
        except Exception as exc:  # noqa: BLE001 - fall back to the per-observation paths below
            ctx.notes.append(f"collector.exports_map failed: {type(exc).__name__}: {exc}")
    for name, obs in named.items():
        if obs.status != "ok" or name in exports:
            continue
        path = _export_file(ctx, obs, name)
        if not path.is_file():
            path = ctx.exports_dir / f"{name}.json"
            path.write_text(json.dumps(obs.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                            encoding="utf-8", newline="\n")
            ctx.notes.append(f"analyst wrote missing export {name}.json (the Collector should export it)")
        exports[name] = str(path)
    for extra in ("openapi_paid_ops", "settlements", "chain_facts", *WORLD_EXPORT_FILES):
        path = ctx.exports_dir / f"{extra}.json"
        if path.is_file():
            exports.setdefault(extra, str(path))
    return exports


def load_world(ctx: "RunContext", named: dict[str, Observation], chain_facts: list[Any]) -> WorldExports:
    world = WorldExports(now_iso=ctx.start_iso)
    for name, obs in named.items():
        if obs.status == "ok":
            world.raw[name] = obs.payload
    for extra in ("openapi_paid_ops", "settlements"):
        path = ctx.exports_dir / f"{extra}.json"
        if path.is_file():
            try:
                world.raw[extra] = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                ctx.notes.append(f"export {extra}.json is not valid JSON")
    for name in WORLD_EXPORT_FILES:
        path = ctx.exports_dir / f"{name}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
            setattr(world, name.split("-", 1)[1], [r for r in rows if isinstance(r, dict)])
    for fact in chain_facts:
        if isinstance(fact, dict):
            world.chain_facts.append(fact)
        else:
            world.chain_facts.append({"about": getattr(fact, "about", None), "predicate": getattr(fact, "predicate", None),
                                      "value": getattr(fact, "value", None), "source": _source_dict(getattr(fact, "source", None))})
    return world


def _source_dict(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, dict):
        return source
    return {"url": getattr(source, "url", None), "checksum": getattr(source, "checksum", None),
            "at": getattr(source, "at", None), "kind": getattr(source, "kind", None)}


# ---------------------------------------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------------------------------------

def query_params(spec: QuerySpec, params: dict[str, Any]) -> Optional[dict[str, Any]]:
    bound: dict[str, Any] = {}
    for name in spec.params:
        if params.get(name) is None:
            return None
        bound[name] = params[name]
    return bound


def sql_executor(ctx: "RunContext") -> Any:
    """The executor the Analyst queries: the mounted one, or the SQLite fallback installed by run_analyst."""
    return ctx.scratch.get("analyst_executor") or ctx.executor


def run_query(ctx: "RunContext", spec: QuerySpec, params: dict[str, Any]) -> Optional[SqlResult]:
    """Execute one catalog query within the SQL budget; emit `sql` + sql.jsonl; None when skipped or failed (the
    metrics that read it then have one value only, and the Critic's C2 rejects them)."""
    bound = query_params(spec, params)
    if bound is None:
        ctx.notes.append(f"{spec.id} skipped: missing parameter {spec.params}")
        return None
    if not ctx.budget_take("sql", ctx.budget.max_sql, "analyst"):
        return None
    started = ctx.clock.now_ms()
    executor = sql_executor(ctx)
    try:
        result = executor.execute(spec.id, spec.sql, bound, spec.meta)
    except NotImplementedError as exc:
        ctx.recover("analyst", "zetaris_query_fallback", f"{spec.id}: executor {executor.name} not implemented ({exc}); no SQL value")
        return None
    except Exception as exc:  # noqa: BLE001 - a failed statement is a visible degradation, never a silent zero
        ctx.recover("analyst", "zetaris_query_fallback", f"{spec.id}: {type(exc).__name__}: {exc}; no SQL value")
        ctx.scratch["analyst_degraded"] = True
        return None
    elapsed = max(0, ctx.clock.now_ms() - started)
    # Sample runs are byte-for-byte reproducible (SPEC.md I4): the deterministic sample clock supplies the duration;
    # live runs report the backend's own timing (Zetaris timeUsed, SQLite wall clock).
    time_used = elapsed if ctx.config.is_sample() else (result.time_used_ms if result.time_used_ms else elapsed)
    payload: dict[str, Any] = {"query_id": spec.id, "executor": result.executor, "sql_sha256": result.sql_sha256 or spec.sql_sha256,
                               "params": bound, "rowcount": result.rowcount, "time_used_ms": time_used}
    if result.fallback_reason:
        payload["fallback_reason"] = result.fallback_reason
    seq = ctx.log.emit("sql", "analyst", payload)
    ctx.log.side("sql", {"seq": seq, "query_id": spec.id, "executor": result.executor, "sql": spec.sql,
                         "sql_sha256": result.sql_sha256 or spec.sql_sha256, "params": bound, "headers": result.headers,
                         "rowcount": result.rowcount, "rows": result.rows[:50], "time_used_ms": time_used,
                         "zetaris_version": result.zetaris_version, "fallback_reason": result.fallback_reason,
                         "tables": result.tables})
    ctx.scratch.setdefault("sql_seq", {})[spec.id] = seq
    ctx.scratch["sql_ms_total"] = ctx.scratch.get("sql_ms_total", 0) + time_used
    if time_used > SQL_QUERY_TIMEOUT_S * 1000:
        ctx.recover("analyst", "budget_exhausted", f"{spec.id} took {time_used} ms (> {SQL_QUERY_TIMEOUT_S} s)")
    return result


def fallback_to_sqlite(ctx: "RunContext", exports: dict[str, str], reason: str, notes: list[str]) -> bool:
    """The mounted executor cannot build its views: run the same committed SQL on an embedded SQLite executor over
    the same exports, so every metric keeps its SQL half (recovery zetaris_query_fallback, backend sqlite-fallback).
    False when even that fails: the claims then carry one value and the Critic's C2 rejects them."""
    from ..zetaris.executor import SqliteExecutor
    ctx.scratch["analyst_degraded"] = True
    try:
        fallback = SqliteExecutor(ctx.run_dir, ctx.log, fallback_reason=reason)
        ctx.scratch["sql_tables"] = fallback.ensure_views(exports)
    except Exception as exc:  # noqa: BLE001
        ctx.recover("analyst", "zetaris_query_fallback", f"{reason}; the SQLite fallback failed too "
                    f"({type(exc).__name__}: {exc}): no SQL half, the Critic will reject single-computed claims", to="none")
        notes.append(f"no SQL executor: {reason}")
        return False
    ctx.scratch["analyst_executor"] = fallback
    ctx.recover("analyst", "zetaris_query_fallback", f"{reason}; the same SQL runs on SQLite over the same exports", to="sqlite")
    notes.append(f"SQL executor fallback to SQLite: {reason}")
    return True


def run_queries(ctx: "RunContext", plan: Plan, catalog: dict[str, QuerySpec], params: dict[str, Any],
                executor_ok: bool) -> dict[str, SqlResult]:
    results: dict[str, SqlResult] = {}
    if not executor_ok:
        return results
    for qid in plan.query_ids:
        spec = catalog.get(qid)
        if spec is None:
            ctx.notes.append(f"unknown query id {qid} in the plan")
            continue
        if ctx.deadline_hit("analyst"):
            ctx.recover("analyst", "deadline_skip", f"SQL stopped before {qid}: deadline")
            break
        if ctx.scratch.get("sql_ms_total", 0) > SQL_TOTAL_TIMEOUT_S * 1000:
            ctx.recover("analyst", "budget_exhausted", f"total SQL time exceeded {SQL_TOTAL_TIMEOUT_S} s before {qid}")
            break
        result = run_query(ctx, spec, params)
        if result is not None:
            results[qid] = result
    return results


# ---------------------------------------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------------------------------------

def evidence_for(spec: MetricSpec, named: dict[str, Observation], verifications: list[Verification],
                 world: WorldExports) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[str] = set()
    for obs in named.values():
        if obs.status != "ok" or obs.family not in spec.sources or obs.url in seen:
            continue
        seen.add(obs.url)
        evidence.append(Evidence(kind="url", ref=obs.url, sha256=obs.bytes_sha256, captured_at=obs.captured_at,
                                 replayed=obs.replayed, stale=obs.stale, http=obs.http, fixture_file=obs.fixture_file))
    if "base_rpc" in spec.sources:
        for v in verifications:
            if v.status == "verified" and v.basescan_url not in seen:
                seen.add(v.basescan_url)
                evidence.append(Evidence(kind="tx", ref=v.basescan_url, verified=True, replayed=any(o.replayed for o in named.values())))
        for fact in world.chain_facts:
            source = fact.get("source") or {}
            url = source.get("url")
            if url and url not in seen and fact.get("predicate") in (spec.fields + (spec.id,)):
                seen.add(url)
                checksum = str(source.get("checksum") or "").replace("sha256:", "") or None
                evidence.append(Evidence(kind="url", ref=url, sha256=checksum, captured_at=source.get("at"),
                                         replayed=bool(source.get("replayed", any(o.replayed for o in named.values())))))
    return evidence


def coverage_for(spec: MetricSpec, world: WorldExports, params: dict[str, Any], ctx: "RunContext") -> Optional[Coverage]:
    if spec.coverage_rule == "catalog_snapshot":
        snapshot = reducers.raw(world, "catalog_snapshot")
        if isinstance(snapshot, dict):
            items = len(snapshot.get("items") or [])
            total = (snapshot.get("pagination") or {}).get("totalReportedByApi")
            total = int(total) if isinstance(total, (int, float)) else None
            share = round(items / total, 4) if total else None
            return Coverage(items_seen=items, total_reported=total, share=share, basis=SNAPSHOT_BASIS)
        pages = [world.raw[k] for k in sorted(world.raw) if k.startswith("cdp_page_") and isinstance(world.raw[k], dict)]
        if pages:
            items = sum(len(p.get("items") or []) for p in pages)
            total = (pages[0].get("pagination") or {}).get("total")
            limit = (pages[0].get("pagination") or {}).get("limit")
            total = int(total) if isinstance(total, (int, float)) else None
            return Coverage(items_seen=items, total_reported=total, share=round(items / total, 4) if total else None,
                            basis=f"live pages ({len(pages)} x {limit})")
        return None
    if spec.coverage_rule == "search_capped_20":
        rows = reducers.bazaar_search_rows(world, params.get("host"))
        return Coverage(items_seen=len(rows), total_reported=None, share=None, basis="search capped at 20")
    return None


def claim_caveats(ctx: "RunContext", spec: MetricSpec) -> tuple[list[str], dict[str, str]]:
    """(caveat codes, code -> memory id) for a metric: the registry's caveat rules plus every code the pitfall
    memories touching the metric's fields, sources or id require (the same H-MEM policy index the Critic's C3
    gate reads, see agents/pitfalls.py). A claim therefore leaves the Analyst already carrying what memory
    demands; C3 only fires when memory and registry disagree."""
    codes, ids, _ = required_caveats(ctx, list(spec.fields), list(spec.sources), [spec.id], spec.caveat_rules)
    return codes, ids


def compute_metric(ctx: "RunContext", index: int, spec: MetricSpec, params: dict[str, Any], world: WorldExports,
                   named: dict[str, Observation], ok_families: dict[str, list[Observation]],
                   results: dict[str, SqlResult], verifications: list[Verification], all_observations: list[Observation],
                   conflicts_out: list[Conflict], next_conflict: list[int]) -> tuple[Optional[Claim], Optional[dict[str, Any]]]:
    """One metric -> (Claim, None) or (None, unavailable entry)."""
    missing = [fam for fam in spec.required_sources if fam not in ok_families]
    if missing:
        return None, {"metric": spec.id, "reason": "; ".join(family_failure_reason(f, all_observations) for f in missing)}
    missing_exports = [name for name in spec.required_exports if not _export_present(world, name)]
    if missing_exports:
        return None, {"metric": spec.id, "reason": "; ".join(_export_missing_reason(name) for name in missing_exports)}
    params = dict(params)
    params.setdefault("since", window_since(world.now_iso))
    until = world.now_iso[:10]
    try:
        reducer_value = get_reducer(spec.reducer)(world, params)
    except Exception as exc:  # noqa: BLE001 - visible, never a silent zero
        return None, {"metric": spec.id, "reason": f"reducer {spec.reducer} failed: {type(exc).__name__}: {exc}"}
    sql_value: Any = None
    extract = get_extractor(spec.id) if spec.sql_backed else None
    if extract is not None:
        try:
            sql_value = extract(results, params)
        except Exception as exc:  # noqa: BLE001
            ctx.notes.append(f"{spec.id}: SQL extractor failed: {type(exc).__name__}: {exc}")
            sql_value = None
    if sql_value is None and reducer_value is None:
        return None, {"metric": spec.id, "reason": "no value from SQL or reducer (sources ok but empty)"}
    backend: Optional[str] = None
    query_id: Optional[str] = None
    sql_sha: Optional[str] = None
    rows = 0
    log_seq: Optional[int] = None
    primary = results.get(spec.query_id)
    if sql_value is not None and primary is not None:
        backend = backend_label(primary)
        query_id = spec.query_id
        sql_sha = primary.sql_sha256
        rows = primary.rowcount
        log_seq = ctx.scratch.get("sql_seq", {}).get(spec.query_id)
    cross_checked = False
    confidence = CONFIDENCE_NOT_CROSS_CHECKED
    value = reducer_value if reducer_value is not None else sql_value
    conflict_ids: list[str] = []
    if sql_value is not None and reducer_value is not None:
        if values_agree(sql_value, reducer_value, spec.unit):
            cross_checked = True
            confidence = spec.confidence
            value = sql_value
        else:
            confidence = CONFIDENCE_BACKEND_DISAGREEMENT
            value = sql_value
            kid = conflict_id(next_conflict[0])
            next_conflict[0] += 1
            urls = [o.url for o in named.values() if o.status == "ok" and o.family in spec.sources]
            conflicts_out.append(Conflict(
                id=kid, predicate=f"{spec.id}:backend_disagreement", subject=spec.subject_template.format(**params),
                values=[ConflictValue(source=f"sql:{backend or ctx.executor.name}", value=sql_value, url=urls[0] if urls else "",
                                      basis=f"committed query {spec.query_id} on {backend or ctx.executor.name}"),
                        ConflictValue(source=f"reducer:{spec.reducer}", value=reducer_value, url=urls[0] if urls else "",
                                      basis="pure-Python reducer over the same exports")],
                explanation=CONFLICT_EXPLANATIONS["backend_disagreement"], status="open", claim_ids=[claim_id(index)]))
            conflict_ids.append(kid)
    cid = claim_id(index)
    caveats, caveat_ids = claim_caveats(ctx, spec)
    claim = Claim(
        id=cid, metric=spec.id, subject=spec.subject_template.format(**{k: v for k, v in params.items()}),
        statement=render_statement(spec, value, params, until), value=value, unit=spec.unit,
        period=(f"{params['since']}..{until}" if spec.period == "l30d" else spec.period),
        evidence=evidence_for(spec, named, verifications, world),
        derivation=Derivation(agent="analyst", query_id=query_id, sql_sha256=sql_sha, backend=backend,
                              reducer=spec.reducer, rows=rows, fields=list(spec.fields), sources=list(spec.sources),
                              log_seq=log_seq),
        coverage=coverage_for(spec, world, params, ctx),
        caveats=caveats, caveat_memory_ids={c: caveat_ids[c] for c in caveats if c in caveat_ids},
        conflict_ids=conflict_ids, confidence=confidence, status="proposed", cross_checked=cross_checked)
    if log_seq is not None:
        claim.evidence.append(Evidence(kind="log_line", ref=ctx.log.ref(log_seq)))
    seq = ctx.log.emit("claim", "analyst", {
        "claim_id": cid, "metric": spec.id, "subject": claim.subject, "value": value, "unit": spec.unit,
        "backend": backend, "cross_checked": cross_checked, "caveats": caveats,
        "coverage": _coverage_json(claim.coverage), "reducer": spec.reducer, "query_id": query_id,
        "sql_value": sql_value, "reducer_value": reducer_value})
    claim.evidence.append(Evidence(kind="log_line", ref=ctx.log.ref(seq)))
    if claim.derivation is not None and claim.derivation.log_seq is None:
        claim.derivation.log_seq = seq
    ctx.scratch.setdefault("recompute", {})[cid] = {"sql": sql_value, "reducer": reducer_value, "metric": spec.id,
                                                  "params": params}
    return claim, None


def _export_present(world: WorldExports, name: str) -> bool:
    if name.startswith("chain_fact:"):
        predicate = name.split(":", 1)[1]
        return any(f.get("predicate") == predicate for f in world.chain_facts) or \
            any(f.get("predicate") == predicate for f in world.facts if isinstance(f, dict))
    return world.raw.get(name) is not None


def _export_missing_reason(name: str) -> str:
    if name.startswith("chain_fact:"):
        return f"chain fact {name.split(':', 1)[1]} not produced: the Verifier did not query the Base RPC for it"
    return f"export {name}.json missing: the Verifier did not produce it (no settlement decoded)"


def _coverage_json(coverage: Optional[Coverage]) -> Optional[dict[str, Any]]:
    if coverage is None:
        return None
    return {"items_seen": coverage.items_seen, "total_reported": coverage.total_reported, "share": coverage.share,
            "basis": coverage.basis}


# ---------------------------------------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------------------------------------

def _obs_url(named: dict[str, Observation], name: str) -> tuple[str, Optional[str]]:
    for key in reducers.RAW_ALIASES.get(name, (name,)):
        obs = named.get(key)
        if obs is not None:
            return obs.url, obs.captured_at
    return "", None


def compose_conflicts(ctx: "RunContext", plan: Plan, claims: list[Claim], named: dict[str, Observation],
                      world: WorldExports, params: dict[str, Any], start_index: int) -> list[Conflict]:
    """Registry counts -> endpoint_count_for_host; Bazaar quality vs receipts -> calls_30d_for_host; ecosystem_size."""
    conflicts: list[Conflict] = []
    by_metric = {c.metric: c for c in claims}
    index = start_index
    subject_host = f"seller:host:{params.get('host')}" if params.get("host") else "seller:host:?"

    endpoint = by_metric.get("endpoint_count_for_host")
    if endpoint is not None and isinstance(endpoint.value, dict):
        values: list[ConflictValue] = []
        labels = [("cdp_search", "cdp_search_host"), ("store_openapi", "store_openapi"), ("agent402", "agent402_index"),
                  ("vet402", "vet402_resolve"), ("x402_trust", "trust_search")]
        for family, name in labels:
            url, captured = _obs_url(named, name)
            if family == "agent402":
                if "agent402_paid" not in endpoint.value:
                    continue
                value: Any = {"paidToolCount": endpoint.value["agent402_paid"], "toolCount": endpoint.value.get("agent402_tools")}
            else:
                key = {"cdp_search": "cdp_search", "store_openapi": "store_openapi", "vet402": "vet402",
                       "x402_trust": "x402_trust_search"}[family]
                if key not in endpoint.value:
                    continue
                value = endpoint.value[key]
            values.append(ConflictValue(source=family, value=value, url=url, basis=REGISTRY_BASIS[family], captured_at=captured))
        if len(values) >= 2:
            kid = conflict_id(index)
            index += 1
            linked = [c.id for c in claims if c.metric in ("endpoint_count_for_host", "bazaar_listings_for_host")]
            conflicts.append(Conflict(id=kid, predicate="endpoint_count_for_host", subject=subject_host, values=values,
                                      explanation=CONFLICT_EXPLANATIONS["endpoint_count_for_host"], status="explained",
                                      claim_ids=linked))

    calls = by_metric.get("bazaar_calls_30d_for_host")
    ext30 = by_metric.get("external_sales_30d")
    if calls is not None and ext30 is not None and reducers.raw(world, "store_receipts") is not None:
        since = ext30.period.split("..")[0] if ext30.period and ".." in ext30.period else window_since(world.now_iso)
        url_b, cap_b = _obs_url(named, "cdp_search_host")
        url_r, cap_r = _obs_url(named, "store_receipts")
        all_30d = reducers.all_sales_30d(world, {"since": since})
        kid = conflict_id(index)
        index += 1
        conflicts.append(Conflict(
            id=kid, predicate="calls_30d_for_host", subject=subject_host,
            values=[ConflictValue(source="cdp_search", value=calls.value, url=url_b, captured_at=cap_b,
                                  basis="l30DaysTotalCalls summed over the host's Bazaar listings (per-endpoint 30-day counters, search capped at twenty)"),
                    ConflictValue(source="receipts", value=all_30d, url=url_r, captured_at=cap_r,
                                  basis=f"all receipts rows settled since {since} (own-wallet rows included)"),
                    ConflictValue(source="receipts", value=ext30.value, url=url_r, captured_at=cap_r,
                                  basis=f"external receipts rows settled since {since}")],
            explanation=CONFLICT_EXPLANATIONS["calls_30d_for_host"], status="explained",
            claim_ids=[calls.id, ext30.id]))

    size = by_metric.get("catalog_resources")
    trust = by_metric.get("trust_ecosystem_stats")
    if size is not None:
        values = []
        url_s, cap_s = _obs_url(named, "catalog_snapshot")
        values.append(ConflictValue(source="cdp_catalog_snapshot", value=size.value, url=url_s, captured_at=cap_s,
                                    basis="unique resources in the compact snapshot (deduplicated by resource)"))
        snapshot = reducers.raw(world, "catalog_snapshot")
        api_total = (snapshot.get("pagination") or {}).get("totalReportedByApi") if isinstance(snapshot, dict) else None
        page0 = world.raw.get("cdp_page_0")
        if api_total is None and isinstance(page0, dict):
            api_total = (page0.get("pagination") or {}).get("total")
        if api_total is not None:
            url_p, cap_p = _obs_url(named, "cdp_page_0")
            values.append(ConflictValue(source="cdp_bazaar", value=int(api_total), url=url_p or url_s, captured_at=cap_p or cap_s,
                                        basis="pagination.total reported live by the discovery API at capture (moves by the minute)"))
        if trust is not None and isinstance(trust.value, dict) and trust.value:
            url_t, cap_t = _obs_url(named, "trust_stats")
            values.append(ConflictValue(source="x402_trust", value={"endpointsListed": trust.value.get("endpoints_listed"),
                                                                     "endpointsActive": trust.value.get("endpoints_active")},
                                        url=url_t, captured_at=cap_t,
                                        basis="x402-trust index across its own sources, 30-day probe window, delisted endpoints counted in listed"))
        if len(values) >= 2:
            kid = conflict_id(index)
            index += 1
            linked = [c.id for c in (size, trust) if c is not None]
            conflicts.append(Conflict(id=kid, predicate="ecosystem_size", subject="catalog:cdp-bazaar", values=values,
                                      explanation=CONFLICT_EXPLANATIONS["ecosystem_size"], status="explained", claim_ids=linked))

    for conflict in conflicts:
        for claim in claims:
            if claim.id in conflict.claim_ids and conflict.id not in claim.conflict_ids:
                claim.conflict_ids.append(conflict.id)
    return conflicts


def emit_conflicts(ctx: "RunContext", conflicts: list[Conflict]) -> None:
    for k in conflicts:
        ctx.log.emit("conflict", "analyst", {
            "conflict_id": k.id, "predicate": k.predicate, "subject": k.subject,
            "values": [{"source": v.source, "value": v.value, "url": v.url, "basis": v.basis} for v in k.values],
            "explanation": k.explanation, "status": k.status, "claim_ids": list(k.claim_ids)})


# ---------------------------------------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------------------------------------

def run_analyst(ctx: "RunContext", plan: Plan, observations: list[Observation], verifications: list[Verification],
                prior_conflicts: Optional[list[Conflict]] = None, chain_facts: Optional[list[Any]] = None) -> AgentResult:
    prior = list(prior_conflicts or [])
    notes: list[str] = []
    payload: dict[str, Any] = {"claims": [], "conflicts": [], "unavailable": [], "sql_results": {}}
    named = name_observations(plan, observations)
    ok_families = families_ok(observations)
    params = dict(plan.params)
    world = load_world(ctx, named, list(chain_facts or []))
    if "base_rpc" not in ok_families and chain_reached(world, verifications):
        ok_families["base_rpc"] = []
    ctx.scratch["world_exports"] = world
    exports = build_exports(ctx, named)
    catalog = load_catalog(ctx.config.sql_dir)

    executor_ok = True
    if hasattr(ctx.executor, "on_recovery"):
        ctx.executor.on_recovery = ctx.recover
    urls = {name: obs.url for name, obs in named.items() if obs.status == "ok" and obs.url}
    try:
        try:
            tables = ctx.executor.ensure_views(exports, urls)
        except TypeError:
            tables = ctx.executor.ensure_views(exports)
        ctx.scratch["sql_tables"] = tables
        absent = [t.get("name") for t in tables if isinstance(t, dict) and t.get("present") is False]
        if absent:
            notes.append("views without export: " + ", ".join(str(a) for a in absent))
    except Exception as exc:  # noqa: BLE001 - NotImplementedError included: the SQL half moves to SQLite
        executor_ok = fallback_to_sqlite(ctx, exports, f"executor {ctx.executor.name} ensure_views failed: "
                                         f"{type(exc).__name__}: {exc}", notes)

    results = run_queries(ctx, plan, catalog, {**params, "since": window_since(world.now_iso)}, executor_ok)
    payload["sql_results"] = results
    ctx.scratch["sql_results"] = results

    claims: list[Claim] = []
    unavailable: list[dict[str, Any]] = []
    conflicts: list[Conflict] = []
    next_conflict = [len(prior) + 1]
    index = 1
    for metric_id in plan.metrics[:MAX_METRICS_PER_RUN]:
        spec = METRICS.get(metric_id)
        if spec is None:
            notes.append(f"unknown metric {metric_id}")
            continue
        if ctx.deadline_hit("analyst"):
            ctx.recover("analyst", "deadline_skip", f"metrics stopped before {metric_id}: deadline")
            unavailable.append({"metric": metric_id, "reason": "deadline hit before the metric was computed"})
            continue
        claim, missing = compute_metric(ctx, index, spec, params, world, named, ok_families, results, verifications,
                                        observations, conflicts, next_conflict)
        if claim is not None:
            claims.append(claim)
            index += 1
        elif missing is not None:
            unavailable.append(missing)
    conflicts.extend(compose_conflicts(ctx, plan, claims, named, world, params, next_conflict[0]))
    emit_conflicts(ctx, conflicts)
    fallback = ctx.scratch.pop("analyst_executor", None)
    if fallback is not None:                 # C2 recomputes from the recorded SQL value, never re-queries
        fallback.cleanup()
    payload.update({"claims": claims, "conflicts": conflicts, "unavailable": unavailable})
    status = "degraded" if (ctx.scratch.get("analyst_degraded") or not executor_ok) else "ok"
    if not claims and not unavailable:
        status = "degraded"
        notes.append("no metric produced a claim")
    return AgentResult("analyst", status, payload, notes=notes)
