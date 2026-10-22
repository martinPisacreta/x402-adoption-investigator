"""Collector: Plan.fetch_list -> Observations via the Fetcher; evidence index; raw exports (SPEC.md 6.2). Owner: B1.

Executes the fetch_list in priority order (per request timeout 10 s, 2 retries at 0.5 s / 1.5 s on timeout/5xx
inside LiveFetcher, each one a `retry` event; identifying User-Agent). Writes raw bytes to
logs/<run_id>/raw/<name>.<ext> and the parsed payload to logs/<run_id>/exports/<name>.json (what both SQL
backends and the reducers read; what the artifact server serves). Live failure after retries -> the mounted
FallbackFetcher serves the fixture with stale=True and the Collector logs recovery fixture_fallback; no fixture
-> status unavailable + recovery source_unavailable; sample mode miss -> fixture_miss + recovery fixture_miss.
Never fabricates a missing source; never exceeds budget.max_fetches (recovery budget_exhausted); checks the
deadline before every request (recovery deadline_skip). All mandatory sources unavailable -> AgentResult
failed -> the runner raises E_NO_SOURCES.

The catalog snapshot (family cdp_catalog_snapshot) is served from fixtures in every mode (replayed=True) and
does not count as a fetch. base_rpc items are deferred markers: the Verifier builds the JSON-RPC batch from
the receipts tx sample (chain/base_rpc.py). A plan with an empty fetch_list gets the playbook's default list
from sources.registry.default_fetch_list with a visible recovery{planner_fallback}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from .. import USER_AGENT
from ..contracts import AgentResult, Observation, Plan
from ..errors import InvestigatorError
from ..sources import catalog as catalog_mod
from ..sources import normalize
from ..sources.fetcher import FetchError, _ext_for
from ..sources.registry import default_fetch_list

if TYPE_CHECKING:
    from ..runner import RunContext

PROVENANCE_KIND: dict[str, str] = {"receipts": "receipts", "base_rpc": "chain"}   # everything else: registry
OPENAPI_PAID_OPS_EXPORT = "openapi_paid_ops"
NORMALIZED_DIR = "normalized"


# ----------------------------------------------------------------------------------------------------------
# Plan helpers
# ----------------------------------------------------------------------------------------------------------

def catalog_option(ctx: "RunContext") -> str:
    return str(ctx.config.catalog or ctx.inp.options.get("catalog") or "snapshot")


def ensure_fetch_list(ctx: "RunContext", plan: Plan) -> list[dict[str, Any]]:
    """The plan's fetch_list, or the playbook default (logged as recovery planner_fallback) when it is empty."""
    if plan.fetch_list:
        return list(plan.fetch_list)
    items = default_fetch_list(plan.playbook, plan.params, catalog=catalog_option(ctx),
                               bazaar_pages=ctx.budget.bazaar_pages, fixture_mode=ctx.mounts.fetcher == "fixture")
    plan.fetch_list = items
    ctx.recover("collector", "planner_fallback",
                f"plan carried no fetch_list; the collector built the default list for playbook {plan.playbook}",
                playbook=plan.playbook, fetch_count=len(items), names=[i["name"] for i in items])
    return list(items)


def ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda it: int(it.get("priority", 50)))   # stable: ties keep plan order


# ----------------------------------------------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------------------------------------------

def _placeholder(item: dict[str, Any], ctx: "RunContext", status: str, error: str) -> Observation:
    return Observation(name=item["name"], family=item["family"], request_key=item.get("request_key", ""),
                       url=item.get("url", ""), method=item.get("method", "GET"), http=None, bytes_sha256=None,
                       latency_ms=0, captured_at=ctx.clock.now_iso(), replayed=False, stale=False,
                       status=status, error=error, payload=None)  # type: ignore[arg-type]


def fetch_item(ctx: "RunContext", item: dict[str, Any]) -> Observation:
    """One fetch_list item -> Observation (snapshot from fixtures; everything else through ctx.fetcher)."""
    if item.get("family") == "cdp_catalog_snapshot" or item.get("fixture_only"):
        obs = catalog_mod.load_snapshot_observation(ctx.manifest, ctx.config.fixtures_dir, ctx.clock,
                                                    url=item.get("url") or catalog_mod.SNAPSHOT_SOURCE_URL)
        obs.name = item["name"]
        return obs
    if item.get("never_in_a_run"):
        return _placeholder(item, ctx, "unavailable", "never fetched inside a run (SPEC.md 7.1: fixture only)")
    method = str(item.get("method") or "GET").upper()
    if method == "RPC" or item.get("family") == "base_rpc":
        method = "POST"                      # JSON-RPC goes over HTTP POST; "RPC" is the planner's label
    try:
        obs = ctx.fetcher.fetch(item["request_key"], item["url"], method, item.get("body"),
                                float(item.get("timeout") or 10.0))
    except FetchError as exc:   # a bare LiveFetcher without fixture fallback
        obs = _placeholder(item, ctx, "unavailable", f"live fetch failed: {exc}")
        obs.http = exc.http
    obs.name = item["name"]
    obs.family = item["family"]
    return obs


def _raw_bytes(ctx: "RunContext", obs: Observation) -> Optional[bytes]:
    getter = getattr(ctx.fetcher, "raw_bytes", None)
    data = getter(obs.request_key) if getter else None
    if data is None and obs.fixture_file:
        path = ctx.config.repo_root / obs.fixture_file
        if path.is_file():
            data = path.read_bytes()
    return data


WINDOWS_MAX_PATH = 260


def _write_or_explain(path: Path, writer: Callable[[Path], None], stage: str) -> None:
    """Write through `writer`; an OSError becomes E_INTERNAL with the cause spelled out. On Windows a run folder
    whose full path exceeds MAX_PATH (260 characters, LongPathsEnabled off) fails here with a bare
    FileNotFoundError, which this message names (clone into a shorter directory or set LongPathsEnabled)."""
    try:
        writer(path)
    except OSError as exc:
        full = str(path.resolve())
        detail = f"cannot write {path.name}: {exc.strerror or exc}"
        if sys.platform == "win32" and len(full) >= WINDOWS_MAX_PATH - 12:
            detail += (f"; the full path is {len(full)} characters, over the Windows MAX_PATH limit of {WINDOWS_MAX_PATH} "
                       "(clone into a shorter directory or enable LongPathsEnabled)")
        raise InvestigatorError("E_INTERNAL", stage, detail, retryable=False,
                                details={"path": full, "path_length": len(full), "errno": exc.errno}) from exc


def write_raw(ctx: "RunContext", obs: Observation, data: Optional[bytes]) -> Optional[Path]:
    if data is None:
        return None
    ext = _ext_for(obs.payload, obs.fixture_file)
    path = ctx.raw_dir / f"{obs.name}{ext}"
    _write_or_explain(path, lambda p: p.write_bytes(data), ctx.stage or "collector")
    return path


def export_observation(ctx: "RunContext", obs: Observation) -> Path:
    """Write the parsed payload to logs/<run_id>/exports/<name>.json (canonical JSON) and record export_path."""
    path = ctx.exports_dir / f"{obs.name}.json"
    text = json.dumps(obs.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    _write_or_explain(path, lambda p: p.write_text(text, encoding="utf-8", newline="\n"), ctx.stage or "collector")
    obs.export_path = portable_path(ctx, path)
    return path


def portable_path(ctx: "RunContext", path: Path) -> str:
    """logs/<run_id>/exports/<name>.json relative to the repo root when inside it (logs stay machine-independent)."""
    try:
        return path.resolve().relative_to(ctx.config.repo_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def absolute_path(ctx: "RunContext", portable: str) -> Path:
    path = Path(portable)
    return path if path.is_absolute() else (ctx.config.repo_root / path)


def record_observation(ctx: "RunContext", obs: Observation, attempted: bool = True) -> None:
    """fetch event + fetch.jsonl line, raw copy, export, and the recovery that explains any degradation."""
    data = _raw_bytes(ctx, obs) if obs.status == "ok" else None
    raw_path = write_raw(ctx, obs, data) if obs.status == "ok" else None
    if obs.status == "ok":
        export_observation(ctx, obs)
    payload: dict[str, Any] = {
        "name": obs.name, "family": obs.family, "url": obs.url, "method": obs.method, "http": obs.http,
        "bytes": len(data) if data is not None else 0, "sha256": obs.bytes_sha256, "latency_ms": obs.latency_ms,
        "replayed": obs.replayed, "stale": obs.stale, "status": obs.status, "request_key": obs.request_key,
    }
    if obs.fixture_file:
        payload["fixture_file"] = obs.fixture_file
    if obs.error:
        payload["error"] = obs.error
    if attempted:
        ctx.log.emit("fetch", "collector", payload)
    ctx.log.side("fetch", {**payload, "captured_at": obs.captured_at, "export_path": obs.export_path,
                           "raw_path": portable_path(ctx, raw_path) if raw_path else None,
                           "attempted": attempted})
    if obs.status == "ok" and obs.stale:
        ctx.recover("collector", "fixture_fallback", f"{obs.name}: live fetch failed, fixture replayed as stale",
                    name=obs.name, family=obs.family, **({"from": "live", "to": "fixture"}), error=obs.error)
    elif obs.status == "fixture_miss":
        ctx.recover("collector", "fixture_miss", f"{obs.name}: {obs.error}", name=obs.name, family=obs.family)
    elif obs.status == "unavailable" and attempted:
        ctx.recover("collector", "source_unavailable", f"{obs.name}: {obs.error}", name=obs.name, family=obs.family)


def fetch_all(ctx: "RunContext", plan: Plan, fetch_list: Optional[list[dict[str, Any]]] = None) -> list[Observation]:
    """Run every fetch_list item through ctx.fetcher within budget.max_fetches; emit `fetch` events and
    fetch.jsonl lines; catalog snapshot from fixtures; export each observation; derived exports at the end."""
    items = ordered(fetch_list if fetch_list is not None else ensure_fetch_list(ctx, plan))
    if hasattr(ctx.fetcher, "on_retry"):
        ctx.fetcher.on_retry = lambda info: ctx.log.emit("retry", "collector", dict(info))
    observations: list[Observation] = []
    deadline_passed = False
    for item in items:
        name = item.get("name", "?")
        if item.get("deferred"):
            continue
        if deadline_passed or ctx.deadline_hit("collector"):
            deadline_passed = True
            ctx.recover("collector", "deadline_skip", f"{name} skipped: deadline reached before the request",
                        name=name, family=item.get("family"))
            obs = _placeholder(item, ctx, "unavailable", "deadline_skip")
            record_observation(ctx, obs, attempted=False)
            observations.append(obs)
            continue
        is_snapshot = item.get("family") == "cdp_catalog_snapshot" or item.get("fixture_only")
        if not is_snapshot and not ctx.budget_take("fetches", ctx.budget.max_fetches, "collector"):
            obs = _placeholder(item, ctx, "unavailable", f"budget_exhausted: max_fetches {ctx.budget.max_fetches} reached")
            record_observation(ctx, obs, attempted=False)
            observations.append(obs)
            continue
        obs = fetch_item(ctx, item)
        record_observation(ctx, obs, attempted=True)
        observations.append(obs)
    write_derived_exports(ctx, observations)
    return observations


# ----------------------------------------------------------------------------------------------------------
# Derived exports
# ----------------------------------------------------------------------------------------------------------

def flatten_openapi_paid_ops(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenAPI paths (map keys) -> rows {path, operation_id, price_usd, network, asset, pay_to, method} for the
    openapi_paid_ops view (x-payment-info). Python-flattened because Zetaris cannot explode map keys."""
    return normalize.openapi_paid_ops_rows(openapi)


def write_derived_exports(ctx: "RunContext", observations: list[Observation]) -> dict[str, str]:
    """exports/openapi_paid_ops.json from the store_openapi observation (same provenance as its source)."""
    written: dict[str, str] = {}
    for obs in observations:
        if obs.family == "store_openapi" and obs.status == "ok" and isinstance(obs.payload, dict) and "paths" in obs.payload:
            rows = flatten_openapi_paid_ops(obs.payload)
            path = ctx.exports_dir / f"{OPENAPI_PAID_OPS_EXPORT}.json"
            path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                            encoding="utf-8", newline="\n")
            written[OPENAPI_PAID_OPS_EXPORT] = portable_path(ctx, path)
            ctx.log.side("fetch", {"name": OPENAPI_PAID_OPS_EXPORT, "family": obs.family, "derived_from": obs.name,
                                   "request_key": obs.request_key, "url": obs.url, "sha256": obs.bytes_sha256,
                                   "rows": len(rows), "export_path": written[OPENAPI_PAID_OPS_EXPORT],
                                   "replayed": obs.replayed, "stale": obs.stale, "attempted": False})
            break
    return written


def write_normalized_exports(ctx: "RunContext", observations: list[Observation]) -> dict[str, str]:
    """Optional, called by whoever wants flat rows (not part of fetch_all): exports/normalized/<view>.json with
    the VIEWS.md columns, the rows and the provenance of every source that contributed."""
    written: dict[str, str] = {}
    folder = ctx.exports_dir / NORMALIZED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    merged: dict[str, dict[str, Any]] = {}
    for obs in observations:
        for table in normalize.normalize(obs):
            entry = merged.setdefault(table.view, {"view": table.view, "columns": table.columns, "sources": [], "rows": []})
            entry["sources"].append(table.source)
            entry["rows"].extend(table.rows)
    for view, entry in merged.items():
        path = folder / f"{view}.json"
        entry["row_count"] = len(entry["rows"])
        path.write_text(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8", newline="\n")
        written[view] = portable_path(ctx, path)
    return written


def exports_map(ctx: "RunContext", observations: list[Observation]) -> dict[str, str]:
    """{name: absolute export path} for every ok observation plus the derived exports (executor input)."""
    out: dict[str, str] = {}
    for obs in observations:
        if obs.status == "ok" and obs.export_path:
            out[obs.name] = str(absolute_path(ctx, obs.export_path))
    derived = ctx.exports_dir / f"{OPENAPI_PAID_OPS_EXPORT}.json"
    if derived.is_file():
        out[OPENAPI_PAID_OPS_EXPORT] = str(derived)
    return out


# ----------------------------------------------------------------------------------------------------------
# Provenance and summaries
# ----------------------------------------------------------------------------------------------------------

def provenance_for(obs: Observation, run_id: str) -> dict[str, Any]:
    """World Model Provenance for an observation: {kind, at, by, url, checksum, runId} (SPEC.md 6.3 / 9.2).
    Sample mode keeps the ORIGINAL kind and url; `by` names the fixture file."""
    kind = PROVENANCE_KIND.get(obs.family, "registry")
    by = f"fixture:{obs.fixture_file}" if obs.replayed and obs.fixture_file else f"collector:{USER_AGENT}"
    prov: dict[str, Any] = {"kind": kind, "at": obs.captured_at, "by": by, "url": obs.url, "runId": run_id}
    if obs.bytes_sha256:
        prov["checksum"] = f"sha256:{obs.bytes_sha256}"
    return prov


def source_summary(obs: Observation) -> dict[str, Any]:
    """The output.sources[] row for one observation."""
    return {"name": obs.name, "family": obs.family, "url": obs.url, "status": obs.status, "http": obs.http,
            "sha256": obs.bytes_sha256, "captured_at": obs.captured_at, "replayed": obs.replayed,
            "stale": obs.stale, "error": obs.error, "fixture_file": obs.fixture_file, "request_key": obs.request_key}


def collector_status(items: list[dict[str, Any]], observations: list[Observation]) -> tuple[str, list[str]]:
    """ok | degraded | failed per SPEC.md 6.2, with the notes that explain it."""
    by_name = {o.name: o for o in observations}
    mandatory = [it["name"] for it in items if not it.get("optional", True) and not it.get("deferred")]
    deferred = [it["name"] for it in items if it.get("deferred")]
    ok = [o.name for o in observations if o.status == "ok" and not o.stale]
    stale = [o.name for o in observations if o.status == "ok" and o.stale]
    misses = [o.name for o in observations if o.status == "fixture_miss"]
    unavailable = [o.name for o in observations if o.status == "unavailable"]
    notes = [f"{len(ok)} ok, {len(stale)} stale fixture fallback(s), {len(misses)} fixture miss(es), "
             f"{len(unavailable)} unavailable, {len(deferred)} deferred to the verifier"]
    if stale:
        notes.append("stale: " + ", ".join(stale))
    if misses:
        notes.append("fixture_miss: " + ", ".join(misses))
    if unavailable:
        notes.append("unavailable: " + ", ".join(unavailable))
    if deferred:
        notes.append("deferred: " + ", ".join(deferred))
    if mandatory and not any(by_name.get(n) is not None and by_name[n].status == "ok" for n in mandatory):
        notes.append("every mandatory source is unavailable: " + ", ".join(mandatory))
        return "failed", notes
    if stale or misses or unavailable:
        return "degraded", notes
    return "ok", notes


def run_collector(ctx: "RunContext", plan: Plan) -> AgentResult:
    items = ensure_fetch_list(ctx, plan)
    observations = fetch_all(ctx, plan, items)
    if not observations:
        return AgentResult("collector", "failed", [], notes=["fetch_list had no fetchable item (all deferred?)"])
    status, notes = collector_status(items, observations)
    return AgentResult("collector", status, observations, notes=notes)  # type: ignore[arg-type]
