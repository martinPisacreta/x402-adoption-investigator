"""Critic: deterministic checks C1-C7, narrative pass B, optional generator second opinion (SPEC.md 6.7). Owner: B4.

Pass A (claims), each firing check logged as a `critique` event plus one accept summary per rule:
- C1 evidence-present: >= 1 evidence item resolves (url sha256 seen by a fetch this run or in the MANIFEST; tx
  verified by the Verifier; log://<run_id>#<seq> exists). Missing -> reject. A metric with
  evidence_rule "decoded_settlement" (settlements_verified, verified_tx) also needs >= 1 sampled settlement of
  the claim's OWN subject (the seller, or the one wallet) that the Verifier actually decoded, verified or failed:
  a count computed while every sampled settlement of the subject was unverifiable (RPC down, fixture miss) is
  a number about missing data, typically a zero that would read as "not verified" -> reject.
- C2 recompute (dual computation): the reducer is re-run over the exports and must equal the SQL value the
  Analyst recorded from the committed query (exact for counts/sums, 1e-6 for shares). A number survives only when
  BOTH halves exist and agree: no SQL value (query skipped, failed or over budget), no reducer value, or a
  disagreement -> reject. A disagreement also stays visible as the open Conflict <metric>:backend_disagreement
  the Analyst opened, which the answer still cites.
- C3 pitfall gate (H-MEM): every pitfall memory whose entities intersect the claim's derivation.fields/sources or
  its metric id requires its caveat code on the claim with caveat_memory_ids[code] == memory.id; missing -> revise
  (the caveat is attached, the claim becomes `revised` and is re-checked next round); present -> candidate
  `helpful` feedback. The Analyst reads the same index when it builds a claim (agents/pitfalls.py), so this gate
  fires only when the two disagree: a memory added after the registry was written, a generator-written claim that
  dropped a caveat, a registry entry out of step with memory. It is a safety net, not a scheduled first round.
- C4 scope (code rules, no memory needed): a derivation that sums l30DaysUniquePayers is rejected outright (the
  memory id that documents the pitfall is cited when present); shares carry numerator and denominator;
  receipts-only claims carry self_reported_receipts; catalog-wide claims carry coverage.
- C5 staleness: replayed evidence older than 7 days -> confidence cap 0.8 and a mandatory disclaimer sentence;
  stale (fallback) evidence -> cap 0.7.
- C6 memory consistency: factual memories about the same metric/subject -> helpful | wrong | not_helpful.
- C7 conflict citation: a claim whose predicate has an open Conflict must list the conflict id -> revise.
Pass B (narrative): every numeric token of the answer (including formatted variants) maps to a claim value, a
share/numerator/denominator, a formatted variant (percent, USDC), a coverage field or a cited conflict value;
every [cNN] must be a non-rejected claim. The map is logged as `narrative_map`; unmapped tokens -> revise.
The generator second opinion (prompts/critic.txt) can only ADD issues. An internal error inside a check is
`revise` with code critic_internal, never a silent pass. The Critic never approves through the generator.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..clock import parse_iso_ms
from ..contracts import Claim, Conflict, Critique, Observation, Verification, AgentResult
from ..generator import generate, load_prompt, render_prompt, salvage_json_list
from .analyst import format_share_pct, format_usdc, values_agree
from .metrics import METRICS, STALE_EVIDENCE_DAYS
from .pitfalls import SeedMemory as _SeedMemory  # noqa: F401 - kept importable for callers of the old name
from .pitfalls import memory_for_code, pitfall_memories
from .reducers import get_reducer, sales_rows
from .writer import CITATION_RE, Answer, STALE_DISCLAIMER, claims_table

if TYPE_CHECKING:
    from ..runner import RunContext

RULES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "B", "LLM")
NUMBER_TOKEN_KINDS: tuple[str, ...] = ("value", "share", "numerator", "denominator", "formatted", "coverage",
                                       "conflict_value", "date", "verdict")
CONFIDENCE_CAP_OLD = 0.8
CONFIDENCE_CAP_STALE = 0.7
MEMORY_TOLERANCE = 0.05
CRITIC_MAX_TOKENS = 500
FEEDBACK_SEVERITY = {"wrong": 3, "helpful": 2, "not_helpful": 1}

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?Z?)?")
SKIP_RE = re.compile(r"0x[0-9a-fA-F]{6,}|\[c\d{2,}\]|\[k\d{2,}\]|\[mem-[^\]]+\]|https?://\S+|\bq\d{2}_[a-z0-9_]+")
NUMBER_RE = re.compile(r"(?<![\w.\-/:])(\d{1,3}(?:,\d{3})+|\d{1,3}(?: \d{3})+(?![\d])|\d+)(\.\d+)?(?:\s?(%|USDC))?(?![\w\-/])")
CONFLICT_CITE_RE = re.compile(r"\[(k\d{2,})\]")
FIRST_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class CriticResult:
    round: int
    issues: list[dict[str, Any]] = field(default_factory=list)     # {target, rule, verdict, reason, memory_ids, fix?, sentence_text?}
    revise: bool = False
    accepted_ids: list[str] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)
    revised_ids: list[str] = field(default_factory=list)
    narrative_map: dict[str, Any] = field(default_factory=dict)
    memory_feedback: dict[str, str] = field(default_factory=dict)  # memory_id -> helpful | not_helpful | wrong
    memory_reasons: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------
# Pass A checks (each returns a list of issues; an empty list means the check passed for this claim)
# ---------------------------------------------------------------------------------------------------------

def _issue(claim: Claim, rule: str, verdict: str, reason: str, memory_ids: Optional[list[str]] = None,
           fix: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    issue: dict[str, Any] = {"target": claim.id, "rule": rule, "verdict": verdict, "reason": reason,
                             "memory_ids": list(memory_ids or [])}
    if fix:
        issue["fix"] = fix
    return issue


def check_c1_evidence(ctx: "RunContext", claim: Claim, observations: list[Observation],
                      verifications: list[Verification]) -> list[dict[str, Any]]:
    seen_sha = {o.bytes_sha256 for o in observations if o.status == "ok" and o.bytes_sha256}
    if ctx.manifest is not None:
        seen_sha.update(e.sha256 for e in ctx.manifest.requests.values())
    verified = {v.basescan_url for v in verifications if v.status == "verified"}
    resolved = 0
    for ev in claim.evidence:
        if ev.kind == "url" and ev.sha256 and ev.sha256 in seen_sha:
            resolved += 1
        elif ev.kind == "tx" and ev.ref in verified:
            resolved += 1
        elif ev.kind == "log_line":
            match = re.match(rf"^log://{re.escape(ctx.run_id)}#(\d+)$", ev.ref)
            if match and 1 <= int(match.group(1)) <= ctx.log.seq:
                resolved += 1
    if resolved == 0:
        return [_issue(claim, "C1", "reject", f"no evidence item resolves ({len(claim.evidence)} listed)")]
    spec = METRICS.get(claim.metric)
    if spec is not None and spec.evidence_rule == "decoded_settlement":
        return check_c1_decoded_settlements(ctx, claim, verifications)
    return []


def subject_settlements(ctx: "RunContext", claim: Claim, verifications: list[Verification]) -> list[Verification]:
    """The sampled settlements a claim's subject owns: every sampled one for a seller subject, the ones whose
    receipts row names the wallet for a wallet subject (an unverifiable settlement has no decoded payer, so the
    payer comes from the receipts export the Analyst loaded)."""
    if not claim.subject.startswith("wallet:"):
        return list(verifications)
    wallet = claim.subject.rsplit(":", 1)[-1].lower()
    world = ctx.scratch.get("world_exports")
    payer_of = {r["tx_hash"]: r["payer"] for r in (sales_rows(world) if world is not None else []) if r["tx_hash"]}
    return [v for v in verifications if str(v.payer or payer_of.get(str(v.tx).lower(), "")).lower() == wallet]


def check_c1_decoded_settlements(ctx: "RunContext", claim: Claim, verifications: list[Verification]) -> list[dict[str, Any]]:
    owned = subject_settlements(ctx, claim, verifications)
    if any(v.status in ("verified", "failed") for v in owned):
        return []
    reasons: dict[str, int] = {}
    for v in owned:
        key = f"{v.status} {v.reason}" if v.reason else v.status
        reasons[key] = reasons.get(key, 0) + 1
    sampled = ", ".join(f"{k} x{n}" for k, n in sorted(reasons.items())) or "none sampled"
    return [_issue(claim, "C1", "reject",
                   f"{claim.metric}={claim.value!r} for {claim.subject} rests on no decoded settlement of that subject "
                   f"({len(owned)} sampled: {sampled}); an unverifiable settlement is missing evidence, not evidence "
                   "that it failed, so this count is reported as unknown")]


def check_c2_recompute(ctx: "RunContext", claim: Claim, conflicts: list[Conflict]) -> list[dict[str, Any]]:
    record = ctx.scratch.get("recompute", {}).get(claim.id)
    world = ctx.scratch.get("world_exports")
    spec = METRICS.get(claim.metric)
    if record is None or world is None or spec is None:
        return [_issue(claim, "C2", "reject", "no recompute record for this claim (Analyst did not register it)")]
    recomputed = get_reducer(spec.reducer)(world, record.get("params", {}))
    sql_value = record.get("sql")
    if sql_value is None and recomputed is None:
        return [_issue(claim, "C2", "reject", "neither the SQL backend nor the reducer produced a value")]
    if sql_value is None:
        return [_issue(claim, "C2", "reject",
                       f"single computation: no SQL value from {spec.query_id} (query skipped, failed or over budget); "
                       "a number is kept only when SQL and the reducer both compute it and agree")]
    if recomputed is None:
        return [_issue(claim, "C2", "reject", f"single computation: the reducer {spec.reducer} produced no value over the exports")]
    if not values_agree(sql_value, recomputed, claim.unit):
        conflict = next((k.id for k in conflicts if k.predicate == f"{claim.metric}:backend_disagreement"), None)
        where = f"; conflict {conflict} stays open" if conflict else ""
        return [_issue(claim, "C2", "reject",
                       f"SQL value {sql_value!r} and recomputed reducer value {recomputed!r} disagree{where}")]
    if not values_agree(claim.value, sql_value, claim.unit):
        return [_issue(claim, "C2", "reject", f"claim value {claim.value!r} differs from the recomputed value {sql_value!r}")]
    return []


def check_c3_pitfall_gate(ctx: "RunContext", claim: Claim) -> tuple[list[dict[str, Any]], dict[str, str]]:
    fields = list(claim.derivation.fields) if claim.derivation else []
    sources = list(claim.derivation.sources) if claim.derivation else []
    memories, index_source = pitfall_memories(ctx, fields, sources, [claim.metric] if claim.metric else [])
    issues: list[dict[str, Any]] = []
    helpful: dict[str, str] = {}
    for memory in memories:
        for code in memory.caveat_codes():
            if claim.caveat_memory_ids.get(code) == memory.id and code in claim.caveats:
                helpful[memory.id] = f"caveat {code} required and attached to {claim.id}"
            else:
                issues.append(_issue(claim, "C3", "revise",
                                     f"pitfall memory {memory.id} ({index_source}) requires caveat {code} on {claim.id}: {memory.content}",
                                     memory_ids=[memory.id], fix={"add_caveat": code, "memory_id": memory.id}))
    return issues, helpful


def check_c4_scope(ctx: "RunContext", claim: Claim) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    spec = METRICS.get(claim.metric)
    if spec is not None and "l30DaysUniquePayers" in spec.sums:
        memory_id = memory_for_code(ctx, "unique_payers_not_dedupable")
        issues.append(_issue(claim, "C4", "reject",
                             "derivation sums l30DaysUniquePayers across endpoints; per-endpoint unique payers are not additive",
                             memory_ids=[memory_id] if memory_id else []))
    if claim.unit == "share":
        value = claim.value
        if not (isinstance(value, dict) and isinstance(value.get("numerator"), int) and isinstance(value.get("denominator"), int)):
            issues.append(_issue(claim, "C4", "reject", "share without an integer numerator and denominator"))
    sources = set(claim.derivation.sources) if claim.derivation else set()
    if sources == {"receipts"} and "self_reported_receipts" not in claim.caveats:
        memory_id = memory_for_code(ctx, "self_reported_receipts")
        issues.append(_issue(claim, "C4", "revise", "receipts-only claim without the self_reported_receipts caveat",
                             memory_ids=[memory_id] if memory_id else [],
                             fix={"add_caveat": "self_reported_receipts", "memory_id": memory_id}))
    if claim.subject.startswith("catalog:") and claim.coverage is None:
        issues.append(_issue(claim, "C4", "reject", "catalog-wide claim without coverage"))
    return issues


def check_c5_staleness(ctx: "RunContext", claim: Claim) -> tuple[list[dict[str, Any]], Optional[float]]:
    cap: Optional[float] = None
    for ev in claim.evidence:
        if ev.stale:
            cap = CONFIDENCE_CAP_STALE if cap is None else min(cap, CONFIDENCE_CAP_STALE)
        elif ev.replayed and ev.captured_at:
            try:
                age_days = (ctx.start_ms - parse_iso_ms(ev.captured_at)) / 86_400_000
            except ValueError:
                continue
            if age_days > STALE_EVIDENCE_DAYS:
                cap = CONFIDENCE_CAP_OLD if cap is None else min(cap, CONFIDENCE_CAP_OLD)
    if cap is None:
        return [], None
    reason = f"evidence older than {STALE_EVIDENCE_DAYS} days or stale: confidence capped at {cap}"
    return [_issue(claim, "C5", "accept", reason)], cap


def _first_number(text: str) -> Optional[float]:
    match = FIRST_NUMBER_RE.search(text.replace(",", ""))
    return float(match.group(0)) if match else None


def _claim_number(claim: Claim) -> Optional[float]:
    value = claim.value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("numerator"), (int, float)):
        return float(value["numerator"])
    return None


def check_c6_memory_consistency(ctx: "RunContext", claim: Claim, answer: Answer) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    hmem = ctx.hmem
    if hmem is None:
        return [], {}, {}
    try:
        result = hmem.query(claim.statement, threshold=0.35, top_n=3, actor="critic")
    except NotImplementedError:
        return [], {}, {}
    hits = list(result.memories)
    if hits:
        seq = ctx.log.emit("memory", "critic", {"op": "retrieve", "actor": "critic", "memory_ids": [h.memory.id for h in hits],
                                                "details": {"claim_id": claim.id, "threshold": 0.35, "top_n": 3}})
        trace = dict(result.trace or {})
        trace["seq"] = seq
        ctx.log.side("hmem.trace", trace)
        for hit in hits:
            ctx.scratch.setdefault("retrievals", []).append({"memory_id": hit.memory.id, "actor": "critic",
                                                             "score": round(float(hit.score), 4)})
    feedback: dict[str, str] = {}
    reasons: dict[str, str] = {}
    issues: list[dict[str, Any]] = []
    cited = set(answer.memory_ids_cited) | set(claim.caveat_memory_ids.values())
    for hit in hits:
        memory = hit.memory
        if getattr(memory, "type", "") != "factual":
            continue
        tags = set(getattr(memory, "tags", []))
        same_predicate = f"metric:{claim.metric}" in tags or claim.metric in memory.content
        same_subject = f"subject:{claim.subject}" in tags or claim.subject in memory.content
        if not (same_predicate and same_subject):
            continue
        remembered = _first_number(memory.content)
        current = _claim_number(claim)
        if remembered is not None and current is not None:
            if abs(remembered - current) <= MEMORY_TOLERANCE * max(abs(current), 1e-9):
                feedback[memory.id] = "helpful"
                reasons[memory.id] = f"remembered value {remembered} matches {claim.id}"
                continue
            if claim.confidence >= 0.9:
                feedback[memory.id] = "wrong"
                reasons[memory.id] = f"remembered value {remembered} differs by more than 5 % from {claim.id} ({current})"
                issues.append(_issue(claim, "C6", "accept", f"memory {memory.id} contradicted by {claim.id}; marked wrong",
                                     memory_ids=[memory.id]))
                continue
        if memory.id not in cited:
            feedback[memory.id] = "not_helpful"
            reasons[memory.id] = f"retrieved for {claim.id} but not cited"
    return issues, feedback, reasons


def check_c7_conflict_citation(ctx: "RunContext", claim: Claim, open_conflicts: list[Conflict]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for conflict in open_conflicts:
        if conflict.status == "resolved":
            continue
        involved = claim.id in conflict.claim_ids or conflict.predicate == claim.metric
        if involved and conflict.id not in claim.conflict_ids:
            issues.append(_issue(claim, "C7", "revise", f"claim is involved in conflict {conflict.id} ({conflict.predicate}) but does not cite it",
                                 fix={"add_conflict": conflict.id}))
    return issues


# ---------------------------------------------------------------------------------------------------------
# Pass B: narrative tokens -> claim values
# ---------------------------------------------------------------------------------------------------------

def _text_leaves(value: Any) -> list[str]:
    """Every string leaf of a claim value (unit `text` values such as "cuit-ar=3, normativa-ar=2", dict/list
    members that are strings). Numbers printed inside them are claim values for pass B."""
    leaves: list[str] = []
    if isinstance(value, str):
        leaves.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            leaves.extend(_text_leaves(item))
    elif isinstance(value, list):
        for item in value:
            leaves.extend(_text_leaves(item))
    return leaves


def _numeric_leaves(value: Any) -> list[tuple[str, Any]]:
    """(path, number) for every numeric leaf of a claim value."""
    leaves: list[tuple[str, Any]] = []
    if isinstance(value, bool):
        return leaves
    if isinstance(value, (int, float)):
        leaves.append(("value", value))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                leaves.append((key, item))
            elif isinstance(item, (dict, list)):
                leaves.extend((f"{key}.{p}", v) for p, v in _numeric_leaves(item))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            leaves.extend((f"{i}.{p}", v) for p, v in _numeric_leaves(item))
    return leaves


def _number_strings(number: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(number, int):
        out.add(str(number))
        out.add(f"{number:,}")
    else:
        out.add(repr(float(number)))
        out.add(f"{float(number):.4f}".rstrip("0").rstrip("."))
        out.add(f"{float(number):.2f}")
        out.add(f"{float(number):.1f}")
        if float(number).is_integer():
            out.add(str(int(number)))
    return {s.replace(",", "") for s in out}


def _dates_in(text: Any) -> set[str]:
    return set(DATE_RE.findall(str(text))) if text is not None else set()


def build_token_domain(claims: list[Claim], conflicts: list[Conflict], verdict_confidence: Optional[float]) -> tuple[dict[str, tuple[Optional[str], str]], dict[str, set[str]], set[str]]:
    """Returns (number -> (claim_id, kind)), (conflict id -> number strings), date strings."""
    domain: dict[str, tuple[Optional[str], str]] = {}
    dates: set[str] = set()

    def put(text: str, claim_id: Optional[str], kind: str) -> None:
        domain.setdefault(text, (claim_id, kind))

    for claim in claims:
        if claim.status == "rejected":
            continue
        for path, number in _numeric_leaves(claim.value):
            kind = path if path in ("share", "numerator", "denominator") else "value"
            for text in _number_strings(number):
                put(text, claim.id, kind)
            if path == "share" and number is not None:
                put(format_share_pct(number), claim.id, "formatted")
                put(f"{float(number) * 100:.2f}", claim.id, "formatted")
                put(f"{float(number) * 100:.0f}", claim.id, "formatted")
        for text_leaf in _text_leaves(claim.value):
            for match in NUMBER_RE.findall(SKIP_RE.sub(" ", text_leaf)):
                put(match[0].replace(",", "").replace(" ", "") + (match[1] or ""), claim.id, "value")
        if claim.unit == "atomic_usdc" and isinstance(claim.value, (int, float)):
            put(format_usdc(claim.value), claim.id, "formatted")
        if claim.coverage is not None:
            for number in (claim.coverage.items_seen, claim.coverage.total_reported, claim.coverage.share):
                if isinstance(number, (int, float)) and not isinstance(number, bool):
                    for text in _number_strings(number):
                        put(text, claim.id, "coverage")
            if claim.coverage.share is not None:
                put(format_share_pct(claim.coverage.share), claim.id, "coverage")
            for text in NUMBER_RE.findall(claim.coverage.basis):
                put(text[0].replace(",", "").replace(" ", "") + (text[1] or ""), claim.id, "coverage")
            dates |= _dates_in(claim.coverage.basis)
        dates |= _dates_in(claim.value) | _dates_in(claim.period) | _dates_in(claim.statement)
        for ev in claim.evidence:
            dates |= _dates_in(ev.captured_at)
    conflict_numbers: dict[str, set[str]] = {}
    for conflict in conflicts:
        numbers: set[str] = set()
        for cv in conflict.values:
            for _, number in _numeric_leaves(cv.value):
                numbers |= _number_strings(number)
            for text in NUMBER_RE.findall(cv.basis):
                numbers.add(text[0].replace(",", "").replace(" ", "") + (text[1] or ""))
            dates |= _dates_in(cv.basis) | _dates_in(cv.captured_at)
        for text in NUMBER_RE.findall(conflict.explanation):
            numbers.add(text[0].replace(",", "").replace(" ", "") + (text[1] or ""))
        conflict_numbers[conflict.id] = numbers
    if verdict_confidence is not None:
        put(f"{verdict_confidence:.2f}", None, "verdict")
    return domain, conflict_numbers, dates


def tokenize_numbers(text: str) -> list[dict[str, Any]]:
    """Numeric tokens of a sentence: ISO dates/timestamps as whole tokens, then numbers incl. formatted variants
    ("12.5 %", "0.001 USDC", "1,234", "1 234 567"); ids, hex, citations and URLs are skipped."""
    tokens: list[dict[str, Any]] = []
    cleaned = SKIP_RE.sub(" ", text)
    for match in DATE_RE.finditer(cleaned):
        tokens.append({"token": match.group(0), "normalized": match.group(0), "kind_hint": "date"})
    cleaned = DATE_RE.sub(" ", cleaned)
    for match in NUMBER_RE.finditer(cleaned):
        integer, fraction, suffix = match.group(1), match.group(2) or "", match.group(3)
        normalized = integer.replace(",", "").replace(" ", "") + fraction
        tokens.append({"token": match.group(0).strip(), "normalized": normalized,
                       "kind_hint": "percent" if suffix == "%" else ("usdc" if suffix == "USDC" else "number")})
    return tokens


def narrative_pass_b(ctx: "RunContext", answer: Answer, claims: list[Claim], conflicts: list[Conflict], round_no: int,
                     verdict_confidence: Optional[float] = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Map every numeric token and [cNN] citation; log `narrative_map`; return (map, issues)."""
    domain, conflict_numbers, dates = build_token_domain(claims, conflicts, verdict_confidence)
    live_ids = {c.id for c in claims if c.status != "rejected"}
    mapped: list[dict[str, Any]] = []
    unsourced: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for index, sentence in enumerate(answer.sentences):
        cited_conflicts = CONFLICT_CITE_RE.findall(sentence)
        for cid in CITATION_RE.findall(sentence):
            if cid not in live_ids:
                unsourced.append({"sentence_index": index, "token": f"[{cid}]", "reason": "dangling citation"})
                issues.append({"target": index, "rule": "B", "verdict": "revise", "memory_ids": [],
                               "reason": f"sentence {index} cites {cid}, which is not an accepted claim", "sentence_text": sentence})
        for token in tokenize_numbers(sentence):
            norm = token["normalized"]
            entry: dict[str, Any] = {"token": token["token"], "claim_id": None, "kind": None, "sentence_index": index}
            if token["kind_hint"] == "date":
                if any(norm.startswith(d) or d.startswith(norm) for d in dates):
                    entry.update(kind="date")
            elif norm in domain:
                claim_id, kind = domain[norm]
                entry.update(claim_id=claim_id, kind=kind)
            else:
                for kid in cited_conflicts:
                    if norm in conflict_numbers.get(kid, set()):
                        entry.update(claim_id=kid, kind="conflict_value")
                        break
            mapped.append(entry)
            if entry["kind"] is None:
                unsourced.append({"sentence_index": index, "token": token["token"], "reason": "no claim value matches"})
                issues.append({"target": index, "rule": "B", "verdict": "revise", "memory_ids": [],
                               "reason": f"sentence {index} contains the number {token['token']!r} that maps to no accepted claim",
                               "sentence_text": sentence})
    narrative_map = {"round": round_no, "tokens": mapped, "unsourced": unsourced}
    ctx.log.emit("narrative_map", "critic", narrative_map)
    return narrative_map, issues


# ---------------------------------------------------------------------------------------------------------
# Narrative-level requirements (caveats, conflicts, disclaimer must be visible in the text)
# ---------------------------------------------------------------------------------------------------------

def narrative_requirements(answer: Answer, claims: list[Claim], conflicts: list[Conflict], stale_cap: bool) -> list[dict[str, Any]]:
    text = answer.text
    issues: list[dict[str, Any]] = []
    for claim in claims:
        if claim.status == "rejected":
            continue
        for code in claim.caveats:
            if f"[{code}]" not in text:
                issues.append({"target": claim.id, "rule": "C3", "verdict": "revise", "memory_ids": [m for c, m in claim.caveat_memory_ids.items() if c == code],
                               "reason": f"the answer does not carry caveat [{code}] required by {claim.id}"})
                break
    for conflict in conflicts:
        if f"[{conflict.id}]" not in text:
            issues.append({"target": conflict.id, "rule": "C7", "verdict": "revise", "memory_ids": [],
                           "reason": f"conflict {conflict.id} ({conflict.predicate}) is not mentioned with its id"})
    if stale_cap and "Disclaimer" not in text:
        issues.append({"target": "narrative", "rule": "C5", "verdict": "revise", "memory_ids": [],
                       "reason": "stale or old evidence requires the disclaimer sentence"})
    return issues


# ---------------------------------------------------------------------------------------------------------
# Generator second opinion
# ---------------------------------------------------------------------------------------------------------

def second_opinion(ctx: "RunContext", answer: Answer, claims: list[Claim], round_no: int) -> list[dict[str, Any]]:
    numbered = "\n".join(f"{i}: {s}" for i, s in enumerate(answer.sentences))
    prompt = render_prompt(load_prompt("critic"), {"answer": numbered, "claims_table": claims_table(claims)})
    result = generate(ctx, "critic", f"critic-r{round_no}", prompt, max_tokens=CRITIC_MAX_TOKENS, attempt=round_no)
    if result is None:
        return []
    items, salvaged = salvage_json_list(result.text)
    if items is None:
        ctx.recover("critic", "generator_fallback", f"critic-r{round_no}: reply is not a JSON array; no issues added")
        return []
    if salvaged:
        # The reply was cut off mid-array: its complete items are kept (the second opinion can only add issues).
        ctx.recover("critic", "generator_reply_repaired",
                    f"critic-r{round_no}: reply was a truncated JSON array; its {salvaged} complete item(s) were kept",
                    prompt_id=f"critic-r{round_no}", kept=salvaged)
    issues: list[dict[str, Any]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        index = item.get("sentence_index")
        if not isinstance(index, int) or not (0 <= index < len(answer.sentences)):
            continue
        issues.append({"target": index, "rule": "LLM", "verdict": "revise", "memory_ids": [],
                       "reason": str(item.get("reason") or "unsupported statement")[:300],
                       "claim_id": item.get("claim_id"), "sentence_text": answer.sentences[index]})
    return issues


# ---------------------------------------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------------------------------------

def _apply_fix(claim: Claim, fix: dict[str, Any]) -> None:
    code = fix.get("add_caveat")
    if code:
        if code not in claim.caveats:
            claim.caveats.append(code)
        if fix.get("memory_id"):
            claim.caveat_memory_ids[code] = fix["memory_id"]
    kid = fix.get("add_conflict")
    if kid and kid not in claim.conflict_ids:
        claim.conflict_ids.append(kid)


def _merge_feedback(result: CriticResult, feedback: dict[str, str], reasons: dict[str, str]) -> None:
    for memory_id, kind in feedback.items():
        current = result.memory_feedback.get(memory_id)
        if current is None or FEEDBACK_SEVERITY[kind] > FEEDBACK_SEVERITY[current]:
            result.memory_feedback[memory_id] = kind
            result.memory_reasons[memory_id] = reasons.get(memory_id, "")


def run_critic(ctx: "RunContext", answer: Answer, claims: list[Claim], verifications: list[Verification],
               observations: list[Observation], round_no: int, conflicts: Optional[list[Conflict]] = None,
               verdict_confidence: Optional[float] = None) -> AgentResult:
    conflicts = list(conflicts if conflicts is not None else ctx.scratch.get("conflicts", []))
    result = CriticResult(round=round_no)
    counts: dict[str, int] = {rule: 0 for rule in RULES}
    stale_cap_any = False
    notes: list[str] = []

    for claim in claims:
        if claim.status == "rejected":
            continue
        issues: list[dict[str, Any]] = []
        checks = [
            ("C1", lambda: check_c1_evidence(ctx, claim, observations, verifications)),
            ("C2", lambda: check_c2_recompute(ctx, claim, conflicts)),
            ("C4", lambda: check_c4_scope(ctx, claim)),
            ("C7", lambda: check_c7_conflict_citation(ctx, claim, conflicts)),
        ]
        for rule, fn in checks:
            try:
                found = fn()
            except Exception as exc:  # noqa: BLE001 - never a silent pass
                found = [_issue(claim, rule, "revise", f"critic_internal: {type(exc).__name__}: {exc}")]
            if not found:
                counts[rule] += 1
            issues.extend(found)
        try:
            c3_issues, helpful = check_c3_pitfall_gate(ctx, claim)
        except Exception as exc:  # noqa: BLE001
            c3_issues, helpful = [_issue(claim, "C3", "revise", f"critic_internal: {type(exc).__name__}: {exc}")], {}
        if not c3_issues:
            counts["C3"] += 1
        issues.extend(c3_issues)
        _merge_feedback(result, {m: "helpful" for m in helpful}, helpful)
        try:
            c5_issues, cap = check_c5_staleness(ctx, claim)
        except Exception as exc:  # noqa: BLE001
            c5_issues, cap = [_issue(claim, "C5", "revise", f"critic_internal: {type(exc).__name__}: {exc}")], None
        if cap is not None:
            stale_cap_any = True
            claim.confidence = min(claim.confidence, cap)
        else:
            counts["C5"] += 1
        issues.extend(c5_issues)
        if round_no == 1:
            try:
                c6_issues, feedback, reasons = check_c6_memory_consistency(ctx, claim, answer)
            except Exception as exc:  # noqa: BLE001
                c6_issues, feedback, reasons = [_issue(claim, "C6", "revise", f"critic_internal: {type(exc).__name__}: {exc}")], {}, {}
            if not c6_issues:
                counts["C6"] += 1
            issues.extend(c6_issues)
            _merge_feedback(result, feedback, reasons)
        else:
            counts["C6"] += 1

        verdict = "accept"
        if any(i["verdict"] == "reject" for i in issues):
            verdict = "reject"
        elif any(i["verdict"] == "revise" for i in issues):
            verdict = "revise"
        for issue in issues:
            seq = ctx.log.emit("critique", "critic", {"round": round_no, "rule": issue["rule"], "target": claim.id,
                                                      "verdict": issue["verdict"], "reason": issue["reason"],
                                                      "memory_ids": issue.get("memory_ids", [])})
            claim.critique.append(Critique(round=round_no, rule=issue["rule"], verdict=issue["verdict"],
                                           reason=issue["reason"], memory_ids=list(issue.get("memory_ids", []))))
            if issue["verdict"] != "accept":
                result.issues.append(issue)
        if verdict == "reject":
            claim.status = "rejected"
            result.rejected_ids.append(claim.id)
        elif verdict == "revise":
            for issue in issues:
                if issue.get("fix"):
                    _apply_fix(claim, issue["fix"])
            claim.status = "revised"
            result.revised_ids.append(claim.id)
        else:
            claim.status = "accepted"
            result.accepted_ids.append(claim.id)
            claim.critique.append(Critique(round=round_no, rule="A", verdict="accept", reason="C1-C7 passed"))

    for rule in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
        ctx.log.emit("critique", "critic", {"round": round_no, "rule": rule, "verdict": "accept", "count": counts[rule]})

    narrative_issues = narrative_requirements(answer, claims, conflicts, stale_cap_any)
    for issue in narrative_issues:
        ctx.log.emit("critique", "critic", {"round": round_no, "rule": issue["rule"], "target": issue["target"],
                                            "verdict": issue["verdict"], "reason": issue["reason"], "memory_ids": issue["memory_ids"]})
    result.issues.extend(narrative_issues)

    try:
        narrative_map, b_issues = narrative_pass_b(ctx, answer, claims, conflicts, round_no, verdict_confidence)
    except Exception as exc:  # noqa: BLE001
        narrative_map, b_issues = {"round": round_no, "tokens": [], "unsourced": []}, [
            {"target": "narrative", "rule": "B", "verdict": "revise", "memory_ids": [],
             "reason": f"critic_internal: {type(exc).__name__}: {exc}"}]
        ctx.log.emit("narrative_map", "critic", narrative_map)
    result.narrative_map = narrative_map
    for issue in b_issues:
        ctx.log.emit("critique", "critic", {"round": round_no, "rule": "B", "target": issue["target"],
                                            "verdict": "revise", "reason": issue["reason"], "memory_ids": []})
    if not b_issues:
        ctx.log.emit("critique", "critic", {"round": round_no, "rule": "B", "verdict": "accept",
                                            "count": len(narrative_map.get("tokens", []))})
    result.issues.extend(b_issues)

    if ctx.generator is not None and ctx.generator.name != "null":
        llm_issues = second_opinion(ctx, answer, claims, round_no)
        for issue in llm_issues:
            ctx.log.emit("critique", "critic", {"round": round_no, "rule": "LLM", "target": issue["target"],
                                                "verdict": "revise", "reason": issue["reason"], "memory_ids": []})
        if not llm_issues:
            ctx.log.emit("critique", "critic", {"round": round_no, "rule": "LLM", "verdict": "accept", "count": 0})
        result.issues.extend(llm_issues)

    result.revise = any(i["verdict"] in ("revise", "reject") for i in result.issues) or bool(result.rejected_ids)
    merged = ctx.scratch.setdefault("memory_feedback", {})
    merged_reasons = ctx.scratch.setdefault("memory_feedback_reasons", {})
    for memory_id, kind in result.memory_feedback.items():
        current = merged.get(memory_id)
        if current is None or FEEDBACK_SEVERITY[kind] > FEEDBACK_SEVERITY[current]:
            merged[memory_id] = kind
            merged_reasons[memory_id] = result.memory_reasons.get(memory_id, "")
    ctx.scratch.setdefault("critic_results", []).append(result)
    return AgentResult("critic", "ok", result, notes=notes)
