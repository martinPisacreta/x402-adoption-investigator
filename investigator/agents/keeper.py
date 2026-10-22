"""Keeper: Observations -> World Model via adapters; IngestReport; exports; second-pass proof (SPEC.md 6.3). Owner: B2.

Pass 1 after the Collector (every `ok` Observation -> IngestObservation with provenance kind/at/by/url/checksum/
runId); chain facts after the Verifier; accepted claims after the Critic as facts of kind "agent" scoped to
investigation:run:<run_id> (source.url = first evidence ref; the claim policy hook refuses anything else and the
Keeper logs critique{kind: provenance_rejected}). The second pass re-ingests the same observation list and must
report eventsAppended 0, changed false (idempotency proof in output.world.second_pass). Exports
world-{entities,relationships,facts,contexts}.json for the reducers and the Critic recompute, plus
exports/world-ingest.json (the observation descriptors scripts/assert_idempotent.py replays).

The World Model is mounted lazily on ctx.world (persist dir = the run dir: world.events.jsonl, world.audit.jsonl,
world.observations.jsonl) so the runner needs no change; ctx.world set by the runner is used as is.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..contracts import AgentResult, Claim, Observation, Verification, to_json
from .collector import absolute_path
from ..worldmodel import WorldModel
from ..worldmodel.adapters import adapter_or_skip, adapter_skip
from ..worldmodel.domain import (PROVENANCE_KIND_BY_FAMILY, day_context, day_context_id, investigation_context_id,
                                 wallet_id)
from ..worldmodel.stable_ids import canonical_json
from ..worldmodel.types import AssertFact, IngestObservation, IngestReport, Provenance, UpsertContext, to_wire

if TYPE_CHECKING:
    from ..runner import RunContext

INGEST_MANIFEST = "world-ingest.json"


# ---- observation mapping ----------------------------------------------------------------------------------

def to_ingest_observation(obs: Observation, run_id: str) -> IngestObservation:
    """Observation -> worldmodel.types.IngestObservation {externalId: <family>:<request_key>, at, source, payload}.

    Provenance keeps the ORIGINAL kind (registry | receipts | chain) and url; in sample mode `by` names the
    fixture file and `checksum` is the sha256 of the fixture bytes, so a live crawl and its replay fold to
    byte-identical world state when the bytes are identical."""
    kind = PROVENANCE_KIND_BY_FAMILY.get(obs.family, "registry")
    if obs.replayed and obs.fixture_file:
        by = f"fixture:fixtures/raw/{obs.fixture_file}"
    else:
        by = f"collector:{obs.name}"
    checksum = f"sha256:{obs.bytes_sha256}" if obs.bytes_sha256 else None
    return IngestObservation(
        external_id=f"{obs.family}:{obs.request_key}", at=obs.captured_at,
        source=Provenance(kind=kind, at=obs.captured_at, by=by, url=obs.url, checksum=checksum, run_id=run_id),
        payload=obs.payload, type=obs.family, title=obs.name)


def ingestable(observations: list[Observation], run_id: str) -> tuple[list[IngestObservation], list[dict[str, Any]]]:
    """Only `ok` observations with a payload are folded; the rest are listed (visible, never a silent zero).
    Duplicate external ids inside one batch get a deterministic #<n> suffix so no observation shadows another."""
    ready: list[IngestObservation] = []
    skipped: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for obs in observations:
        if obs.status != "ok" or obs.payload is None:
            skipped.append({"name": obs.name, "family": obs.family, "status": obs.status, "error": obs.error})
            continue
        item = to_ingest_observation(obs, run_id)
        count = seen.get(item.external_id, 0)
        seen[item.external_id] = count + 1
        if count:
            item.external_id = f"{item.external_id}#{count + 1}"
        ready.append(item)
    return ready, skipped


# ---- world mounting and exports ---------------------------------------------------------------------------

def ensure_world(ctx: "RunContext") -> WorldModel:
    if getattr(ctx, "world", None) is None:
        ctx.world = WorldModel(Path(ctx.run_dir), ctx.clock, ctx.run_id)
    return ctx.world


def export_world(ctx: "RunContext") -> dict[str, str]:
    """Write exports/world-entities.json, world-relationships.json, world-facts.json (canonical JSON); return paths."""
    world = ensure_world(ctx)
    paths = world.export(ctx.exports_dir)
    world.flush()
    return paths


def write_ingest_manifest(ctx: "RunContext", items: list[IngestObservation], observations: list[Observation]) -> Path:
    """exports/world-ingest.json: the descriptors (externalId, at, source, type, title, payload export file) that
    scripts/assert_idempotent.py replays into a fresh World Model."""
    exports = Path(ctx.exports_dir)
    by_name = {o.name: o for o in observations}
    rows = []
    for item in items:
        obs = by_name.get(item.title)
        export_name = f"{item.title}.json"
        export_path = absolute_path(ctx, obs.export_path) if obs is not None and obs.export_path else exports / export_name
        if not export_path.is_file():
            export_path = exports / export_name
            export_path.write_text(canonical_json(item.payload) + "\n", encoding="utf-8", newline="\n")
        rows.append({"externalId": item.external_id, "at": item.at, "source": to_wire(item.source), "type": item.type,
                     "title": item.title, "payloadExport": export_path.name})
    path = exports / INGEST_MANIFEST
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def _ingest_event(ctx: "RunContext", pass_no: Any, report: IngestReport, extra: dict[str, Any]) -> int:
    world = ensure_world(ctx)
    return ctx.log.emit("ingest", "keeper", {"pass": pass_no, "report": report.to_wire(), "quarantined": list(report.quarantined),
                                             "counts": world.counts(), **extra})


def _adapter_skips(items: list[IngestObservation], report: IngestReport) -> list[dict[str, Any]]:
    by_id = {i.external_id: i for i in items}
    return [adapter_skip(by_id[d["externalId"]]) for d in report.details if d["status"] == "no_events" and d["externalId"] in by_id]


# ---- pipeline entry points --------------------------------------------------------------------------------

def run_keeper_ingest(ctx: "RunContext", observations: list[Observation], pass_no: int) -> AgentResult:
    """Ingest pass 1 (or the second pass) and emit the `ingest` event {pass, report, quarantined}."""
    world = ensure_world(ctx)
    items, skipped = ingestable(observations, ctx.run_id)
    report = world.ingest(items, adapter_or_skip)
    skips = _adapter_skips(items, report)
    _ingest_event(ctx, pass_no, report, {"skipped_sources": skipped, "adapter_skips": skips})
    notes = [f"ingested {report.processed} observations, {report.events_appended} events appended"]
    notes += [f"source not folded ({s['status']}): {s['name']}" for s in skipped]
    notes += [f"adapter_skip: {s['name']} ({s['family']}) top-level keys {s['top_level_keys']}" for s in skips]
    notes += [f"quarantined: {q['externalId']}: {q['error']}" for q in report.quarantined]
    if pass_no == 1:
        export_world(ctx)
        write_ingest_manifest(ctx, items, observations)
    status = "degraded" if report.quarantined else "ok"
    return AgentResult("keeper", status, report.summary(), notes=notes)


def chain_fact(ctx: "RunContext", item: Any) -> Optional[AssertFact]:
    """A Verifier chain fact -> AssertFact of kind chain. Accepts a dict {about, predicate, value, url?, at?,
    checksum?, confidence?, context?} or a contracts.Verification (verified ones become onchain_tx facts)."""
    now = ctx.clock.now_iso()
    if isinstance(item, Verification):
        if item.status != "verified" or not item.payer:
            return None
        at = item.block_time or now
        value = {"tx": item.tx, "payer": item.payer.lower(), "seller": (item.seller or "").lower() or None,
                 "amountAtomic": item.amount_atomic, "blockNumber": item.block, "blockTime": item.block_time,
                 "facilitatorFrom": item.facilitator_from, "decodedFrom": item.decoded_from, "basescanUrl": item.basescan_url,
                 "receiptMatch": item.receipt_match}
        return AssertFact(about=wallet_id(item.payer), predicate="onchain_tx", value=value, confidence=0.95, context=day_context_id(at),
                          source=Provenance(kind="chain", at=at, by="verifier", url=item.basescan_url, run_id=ctx.run_id))
    if isinstance(item, dict) and item.get("about") and item.get("predicate"):
        at = str(item.get("at") or now)
        checksum = item.get("checksum")
        if checksum and not str(checksum).startswith("sha256:"):
            checksum = f"sha256:{checksum}"
        return AssertFact(about=str(item["about"]), predicate=str(item["predicate"]), value=to_json(item.get("value")),
                          confidence=float(item.get("confidence", 0.9)), context=item.get("context") or day_context_id(at),
                          source=Provenance(kind=str(item.get("kind") or "chain"), at=at, by=str(item.get("by") or "verifier"),
                                            url=item.get("url"), checksum=checksum, run_id=ctx.run_id))
    return None


def run_keeper_chain_facts(ctx: "RunContext", chain_facts: list[Any]) -> AgentResult:
    """Assert the decoded chain facts (onchain_tx, usdc_balance_atomic, tx_count) with kind chain provenance."""
    world = ensure_world(ctx)
    asserted: list[str] = []
    ignored: list[str] = []
    rejected: list[dict[str, Any]] = []
    days_seen: set[str] = set()
    for item in chain_facts:
        fact = chain_fact(ctx, item)
        if fact is None:
            ignored.append(type(item).__name__ if not isinstance(item, dict) else json.dumps(item, default=str)[:80])
            continue
        try:
            if fact.context and fact.context.startswith("day:utc:") and fact.context not in days_seen:
                days_seen.add(fact.context)
                world.upsert_context(day_context(fact.source.at, fact.source))
            world.assert_fact(fact)
            asserted.append(f"{fact.about}.{fact.predicate}")
        except ValueError as exc:
            rejected.append({"about": fact.about, "predicate": fact.predicate, "error": str(exc)})
    world.flush()
    if asserted:
        export_world(ctx)
    notes = [f"chain facts asserted: {len(asserted)}"]
    notes += [f"chain fact ignored (unrecognised shape or unverified): {i}" for i in ignored]
    notes += [f"chain fact rejected: {r['about']}.{r['predicate']}: {r['error']}" for r in rejected]
    payload = {"chain_facts_asserted": len(asserted), "asserted": asserted, "rejected": rejected, "ignored": len(ignored)}
    return AgentResult("keeper", "degraded" if rejected else "ok", payload, notes=notes)


def input_sha256(ctx: "RunContext") -> Optional[str]:
    inp = getattr(ctx, "inp", None)
    if inp is None:
        return None
    text = json.dumps(to_json(inp), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_fact(ctx: "RunContext", claim: Claim, context_id: str, at: str) -> AssertFact:
    """Accepted claim -> fact of kind agent, source.url = the claim's first evidence ref (URL, basescan or log://)."""
    ref = claim.evidence[0].ref if claim.evidence else None
    by = claim.derivation.agent if claim.derivation is not None else "analyst"
    return AssertFact(about=claim.subject, predicate=claim.metric, value=to_json(claim.value), confidence=float(claim.confidence),
                      context=context_id, source=Provenance(kind="agent", at=at, by=by, url=ref, run_id=ctx.run_id))


def run_keeper_claims(ctx: "RunContext", accepted: list[Claim], observations: list[Observation]) -> AgentResult:
    """Assert accepted claims as agent facts, then re-ingest the observations (second pass) and export the world."""
    world = ensure_world(ctx)
    now = ctx.clock.now_iso()
    context_id = investigation_context_id(ctx.run_id)
    mounts = getattr(ctx, "mounts", None)
    world.upsert_context(UpsertContext(
        type="investigation", id=context_id, source=Provenance(kind="agent", at=now, by="keeper", run_id=ctx.run_id),
        attrs={"runId": ctx.run_id, "inputSha256": input_sha256(ctx), "mode": getattr(mounts, "mode", None),
               "question": getattr(getattr(ctx, "inp", None), "question", None)}, started_at=now))
    asserted: list[str] = []
    refused: list[dict[str, Any]] = []
    round_no = int(getattr(ctx, "counters", {}).get("critic_rounds", 0)) if hasattr(ctx, "counters") else 0
    for claim in accepted:
        fact = claim_fact(ctx, claim, context_id, now)
        try:
            world.assert_fact(fact)
            asserted.append(claim.id)
        except ValueError as exc:
            refused.append({"claim_id": claim.id, "error": str(exc)})
            ctx.log.emit("critique", "keeper", {"round": round_no, "rule": "provenance", "target": claim.id, "verdict": "reject",
                                                "reason": str(exc), "memory_ids": [], "kind": "provenance_rejected"})
    items, skipped = ingestable(observations, ctx.run_id)
    second = world.ingest(items, adapter_or_skip)
    _ingest_event(ctx, 2, second, {"skipped_sources": skipped, "adapter_skips": _adapter_skips(items, second),
                                   "claims_asserted": len(asserted), "claims_refused": refused})
    export_world(ctx)
    notes = [f"claims asserted as agent facts: {len(asserted)}", f"second pass: {second.summary()}"]
    notes += [f"claim refused by the policy hook: {r['claim_id']}: {r['error']}" for r in refused]
    status = "ok" if not refused and not second.quarantined and not second.changed else "degraded"
    payload = {"second_pass": second.summary(), "claims_asserted": len(asserted), "quarantined": len(refused) + len(second.quarantined),
               "claims_refused": refused, "investigation_context": context_id}
    return AgentResult("keeper", status, payload, notes=notes)


def world_summary(first_pass: Optional[dict[str, Any]], claims_payload: Optional[dict[str, Any]],
                  counts: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """The output.world object."""
    claims_payload = claims_payload or {}
    summary = {"first_pass": first_pass, "second_pass": claims_payload.get("second_pass"),
               "quarantined": int(claims_payload.get("quarantined", 0)),
               "claims_asserted": int(claims_payload.get("claims_asserted", 0))}
    if counts:
        summary["counts"] = {k: counts[k] for k in ("entities", "contexts", "relationships", "facts") if k in counts}
    return summary
