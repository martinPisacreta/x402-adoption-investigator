"""Writer: claims -> Answer (SPEC.md section 6.6). Owner: B4.

The template renderer always exists and is the sample-mode default when no transcript is recorded. The generator
path (prompts/writer.txt, max_tokens 700, temperature 0) receives the claims as a JSON table, the conflicts, the
H-MEM reinjection block (actor writer) and the previous round's critic issues; its output is split into
sentences tagged with the [cNN] ids they contain. The verdict label is computed by verdict.py and injected: the
model only phrases it. The Writer never introduces a number that is not a claim value: every number the template
prints comes from a claim value, its share/numerator/denominator, a formatted variant (percent, USDC), a
coverage field or a conflict value, which is exactly the domain the Critic's pass B maps against.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..contracts import AgentResult, Claim, Conflict, Focus
from ..generator import generate, load_prompt, render_prompt
from .analyst import format_share_pct, format_usdc, format_value
from .metrics import CAVEAT_PHRASES, STALE_EVIDENCE_DAYS
from .verdict import Verdict

if TYPE_CHECKING:
    from ..runner import RunContext

CITATION_RE = re.compile(r"\[(c\d{2,})\]")
MEMORY_RE = re.compile(r"\[(mem-[A-Za-z0-9_-]+)\]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[\"'(])")
WRITER_MAX_TOKENS = 700
VERDICT_SENTENCES: dict[str, str] = {
    "adoption_real": "The evidence supports real adoption",
    "adoption_thin": "The evidence shows thin adoption",
    "self_settled_mostly": "Most settlements are the seller paying itself",
    "concentrated": "Paid calls are concentrated in a few hosts",
    "diffuse": "Paid calls are spread across many hosts",
    "insufficient_evidence": "There is not enough accepted evidence to answer",
}
STALE_DISCLAIMER = (f"Disclaimer: some evidence was captured more than {STALE_EVIDENCE_DAYS} days before this run "
                    "or came from a fallback fixture, so the figures may be out of date.")


@dataclass
class Answer:
    verdict: str
    summary: list[str] = field(default_factory=list)      # <= 5 bullets, each citing [cNN]
    sentences: list[str] = field(default_factory=list)    # full narrative, one sentence per item
    memory_ids_cited: list[str] = field(default_factory=list)
    source: str = "template"                                # "template" | "generator" | "recorded-generator"
    round: int = 1
    dropped: list[str] = field(default_factory=list)       # sentences the template left out after critic issues

    @property
    def text(self) -> str:
        return " ".join(self.sentences)

    def citations(self) -> list[str]:
        return sorted({cid for s in self.sentences for cid in CITATION_RE.findall(s)})


# ---------------------------------------------------------------------------------------------------------
# Template renderer
# ---------------------------------------------------------------------------------------------------------

def verdict_sentence(verdict: Verdict) -> str:
    why = ", ".join(f"[{cid}]" for cid in verdict.why)
    base = VERDICT_SENTENCES.get(verdict.label, verdict.label)
    suffix = f" based on {why}" if why else ""
    return f"{base} (verdict {verdict.label}){suffix}."


def claim_sentence(claim: Claim) -> str:
    statement = claim.statement.rstrip(".")
    return f"{statement} [{claim.id}]."


def conflict_sentence(conflict: Conflict, live_ids: Optional[set[str]] = None) -> str:
    """One "where sources disagree" sentence; it cites only claims that are not rejected (a rejected claim is
    never cited, but the conflict itself stays in the answer with its values and explanation)."""
    parts = "; ".join(f"{v.source}: {format_value(v.value)} ({v.basis})" for v in conflict.values)
    cited = " ".join(f"[{cid}]" for cid in conflict.claim_ids if live_ids is None or cid in live_ids)
    return (f"Where sources disagree [{conflict.id}] on {conflict.predicate}: {parts}. "
            f"{conflict.explanation.rstrip('.')} {cited}".rstrip() + ".")


def rejection_rule(claim: Claim) -> str:
    """The rule of the Critic's first reject verdict on a claim (C1-C7, B or A for unresolved)."""
    for item in claim.critique:
        if item.verdict == "reject":
            return item.rule
    return "A"


def rejected_sentence(claims: list[Claim]) -> Optional[str]:
    """Names the claims the Critic rejected (metric and rule, no number: a rejected value is never restated)."""
    rejected = [c for c in claims if c.status == "rejected"]
    if not rejected:
        return None
    names = "; ".join(f"{c.metric} (rule {rejection_rule(c)})" for c in rejected)
    return (f"Rejected by the Critic and left out of this answer: {names}; "
            "the reasons are in rejected_claims[] and in the critique lines of the run log.")


def caveat_sentences(claims: list[Claim]) -> list[str]:
    """One sentence per caveat code, in plain words with the code in brackets, citing the claims it qualifies."""
    by_code: dict[str, list[str]] = {}
    for claim in claims:
        for code in claim.caveats:
            by_code.setdefault(code, []).append(claim.id)
    sentences: list[str] = []
    for code in sorted(by_code):
        phrase = CAVEAT_PHRASES.get(code, code.replace("_", " "))
        memory_ids = sorted({claim.caveat_memory_ids[code] for claim in claims
                             if code in claim.caveat_memory_ids and claim.id in by_code[code]})
        memory = " " + " ".join(f"[{m}]" for m in memory_ids) if memory_ids else ""
        cites = " ".join(f"[{cid}]" for cid in by_code[code])
        sentences.append(f"Caveat [{code}]: {phrase}{memory}; applies to {cites}.")
    return sentences


def needs_stale_disclaimer(claims: list[Claim], now_ms: int) -> bool:
    from ..clock import parse_iso_ms
    for claim in claims:
        for ev in claim.evidence:
            if ev.stale:
                return True
            if ev.replayed and ev.captured_at:
                try:
                    age_days = (now_ms - parse_iso_ms(ev.captured_at)) / 86_400_000
                except ValueError:
                    continue
                if age_days > STALE_EVIDENCE_DAYS:
                    return True
    return False


def render_template(claims: list[Claim], conflicts: list[Conflict], unavailable: list[dict[str, Any]],
                    verdict: Verdict, sample_notice: Optional[str] = None, issues: Optional[list[dict[str, Any]]] = None,
                    stale_disclaimer: bool = False, round_no: int = 1) -> Answer:
    """Deterministic Answer from the claims that are not rejected: verdict sentence, <= 5 bullets, one sentence
    per claim, one "where sources disagree and why" sentence per conflict, caveats, unavailable metrics,
    disclaimer, sample notice. Critic issues of the previous round drop the sentences they flagged."""
    usable = [c for c in claims if c.status != "rejected"]
    dropped = {str(i.get("sentence_text")) for i in (issues or []) if i.get("rule") in ("B", "LLM") and i.get("sentence_text")}
    sentences: list[str] = [verdict_sentence(verdict)]
    summary: list[str] = []
    if not usable:
        sentences.append("No claim was accepted in this run, so no number is reported.")
    key_metrics = _key_claims(usable, verdict)
    for claim in key_metrics[:5]:
        summary.append(claim_sentence(claim))
    for claim in usable:
        sentences.append(claim_sentence(claim))
    live_ids = {c.id for c in usable}
    for conflict in conflicts:
        sentences.append(conflict_sentence(conflict, live_ids))
    sentences.extend(caveat_sentences(usable))
    for item in unavailable:
        sentences.append(unavailable_sentence(item))
    rejected_text = rejected_sentence(claims)
    if rejected_text:
        sentences.append(rejected_text)
    if stale_disclaimer:
        sentences.append(STALE_DISCLAIMER)
    if sample_notice:
        sentences.append(sample_notice)
    left_out = [s for s in sentences if s in dropped]
    sentences = [s for s in sentences if s not in dropped]
    cited = sorted({m for c in usable for m in c.caveat_memory_ids.values()})
    return Answer(verdict=verdict.label, summary=summary, sentences=sentences, memory_ids_cited=cited,
                  source="template", round=round_no, dropped=left_out)


def _key_claims(claims: list[Claim], verdict: Verdict) -> list[Claim]:
    """Verdict claims first, then the rest in Analyst order."""
    by_id = {c.id: c for c in claims}
    ordered = [by_id[cid] for cid in verdict.why if cid in by_id]
    ordered.extend(c for c in claims if c not in ordered)
    return ordered


# ---------------------------------------------------------------------------------------------------------
# Generator path
# ---------------------------------------------------------------------------------------------------------

def claims_table(claims: list[Claim]) -> str:
    rows = [{"id": c.id, "statement": c.statement, "value": c.value, "unit": c.unit, "caveats": c.caveats,
             "conflict_ids": c.conflict_ids,
             "formatted": _formatted_variants(c)} for c in claims if c.status != "rejected"]
    return json.dumps(rows, ensure_ascii=False, indent=1)


def _formatted_variants(claim: Claim) -> dict[str, str]:
    out: dict[str, str] = {}
    if claim.unit == "atomic_usdc":
        out["usdc"] = format_usdc(claim.value)
    if isinstance(claim.value, dict) and "share" in claim.value:
        out["percent"] = format_share_pct(claim.value.get("share"))
    return out


def conflicts_table(conflicts: list[Conflict]) -> str:
    rows = [{"id": k.id, "predicate": k.predicate, "values": [{"source": v.source, "value": v.value, "basis": v.basis}
                                                              for v in k.values], "explanation": k.explanation}
            for k in conflicts]
    return json.dumps(rows, ensure_ascii=False, indent=1) if rows else "(none)"


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return parts


def parse_generated(text: str, verdict: Verdict, round_no: int, recorded: bool) -> Answer:
    bullets: list[str] = []
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped in ("-", "*", "•"):     # blank lines and empty bullets ("- " alone) carry nothing
            continue
        if stripped.startswith(("- ", "* ", "• ")) and len(bullets) < 5:
            bullets.append(stripped[2:].strip())
        else:
            body.append(stripped)
    sentences = split_sentences(" ".join(body))
    memory_ids = sorted({m for s in (bullets + sentences) for m in MEMORY_RE.findall(s)})
    return Answer(verdict=verdict.label, summary=bullets, sentences=bullets + sentences, memory_ids_cited=memory_ids,
                  source="recorded-generator" if recorded else "generator", round=round_no)


def render_with_generator(ctx: "RunContext", claims: list[Claim], conflicts: list[Conflict],
                          unavailable: list[dict[str, Any]], verdict: Verdict, issues: list[dict[str, Any]],
                          reinjection: str, round_no: int) -> Optional[Answer]:
    """prompts/writer.txt -> sentences tagged with [cNN]; None when the generator returns nothing."""
    prompt = render_prompt(load_prompt("writer"), {
        "question": ctx.inp.question, "claims_table": claims_table(claims), "conflicts": conflicts_table(conflicts),
        "unavailable": json.dumps(unavailable, ensure_ascii=False) if unavailable else "(none)",
        "hmem_reinjection": reinjection or "(no memory retrieved)",
        "critic_issues": json.dumps([{k: v for k, v in i.items() if k != "fix"} for i in issues], ensure_ascii=False, indent=1) if issues else "(none)",
        "verdict_label": verdict.label, "verdict_confidence": f"{verdict.confidence:.2f}"})
    result = generate(ctx, "writer", f"writer-r{round_no}", prompt, max_tokens=WRITER_MAX_TOKENS, attempt=round_no,
                      reinjection=reinjection, memory_ids=list(ctx.scratch.get("writer_memory_ids", [])))
    if result is None:
        return None
    answer = parse_generated(result.text, verdict, round_no, result.recorded)
    if not answer.sentences:
        ctx.recover("writer", "generator_fallback", f"writer-r{round_no}: empty narrative", to="template")
        return None
    return answer


def retrieve_memories(ctx: "RunContext", question: str) -> tuple[str, list[str]]:
    """H-MEM query(question, 0.35, 5, actor writer) -> (reinjection block, ids)."""
    from .planner import retrieve_memories as _retrieve
    return _retrieve(ctx, question, Focus(), actor="writer")


# ---------------------------------------------------------------------------------------------------------
# Trimming after the last round
# ---------------------------------------------------------------------------------------------------------

def trim_unsourced(ctx: "RunContext", answer: Answer, critic_result: Any, claims: list[Claim]) -> Answer:
    """After the last round: cut the sentences pass B flagged (recovery narrative_trimmed) and refill from
    accepted claims only, through the template renderer."""
    flagged: set[int] = set()
    for issue in getattr(critic_result, "issues", []):
        if issue.get("rule") in ("B", "LLM") and isinstance(issue.get("target"), int):
            flagged.add(int(issue["target"]))
    if not flagged:
        return answer
    kept = [s for i, s in enumerate(answer.sentences) if i not in flagged]
    cut = [answer.sentences[i] for i in sorted(flagged) if i < len(answer.sentences)]
    ctx.recover("writer", "narrative_trimmed", f"{len(cut)} sentence(s) cut after the last critic round",
                sentences=[c[:160] for c in cut])
    accepted = [c for c in claims if c.status == "accepted"]
    cited = {cid for s in kept for cid in CITATION_RE.findall(s)}
    refill = [claim_sentence(c) for c in accepted if c.id not in cited]
    summary = [b for b in answer.summary if b not in cut]
    return Answer(verdict=answer.verdict, summary=summary, sentences=kept + refill,
                  memory_ids_cited=answer.memory_ids_cited, source=answer.source + "+trimmed", round=answer.round)


def unavailable_sentence(item: dict[str, Any]) -> str:
    return f"Unavailable: {item.get('metric')} ({item.get('reason')})."


def sample_notice(ctx: "RunContext") -> Optional[str]:
    """The sentence every sample-mode answer ends with (None in live mode)."""
    if not ctx.config.is_sample():
        return None
    captured = ctx.mounts.fixtures_captured_at or "unknown date"
    return f"Sample mode: this answer replays committed fixtures captured {captured}; no network, no model calls."


def complete_requirements(ctx: "RunContext", answer: Answer, critic_result: Any, claims: list[Claim],
                          conflicts: list[Conflict], unavailable: list[dict[str, Any]]) -> Answer:
    """After the last round: the code-owned sentences a final answer must carry are added back from the claims
    when the answer lacks them (recovery narrative_completed): a "where sources disagree" sentence for each
    conflict not cited as [kNN] (rule C7), a caveat sentence for each caveat code of a non-rejected claim not
    carried as [code] (rule C3, read from the claims: C3 reports only the first missing code of a claim), an
    "Unavailable:" sentence per unavailable metric, the sentence naming the rejected claims, the stale-evidence
    disclaimer when it is due (C5) and the sample-mode notice. A generated draft can leave these out, and the
    Critic's model second opinion can flag them (its prompt shows the accepted claims only), so trim_unsourced may
    have cut them; a sentence that rule B flagged in the last round, or that the template left out after an issue,
    is never put back. Nothing is removed and no number is introduced that is not a claim, conflict or coverage
    value. An answer that already carries everything (every template answer) is returned unchanged."""
    text = answer.text
    usable = [c for c in claims if c.status != "rejected"]
    excluded = set(answer.dropped) | {str(i.get("sentence_text")) for i in getattr(critic_result, "issues", []) or []
                                      if i.get("rule") == "B" and i.get("sentence_text")}
    present = set(answer.sentences)
    missing_codes = sorted({code for c in usable for code in c.caveats if f"[{code}]" not in text})
    live_ids = {c.id for c in usable}
    middle = [conflict_sentence(k, live_ids) for k in conflicts if f"[{k.id}]" not in text]
    middle += [s for s in caveat_sentences(usable) if any(s.startswith(f"Caveat [{code}]:") for code in missing_codes)]
    middle += [unavailable_sentence(item) for item in unavailable
               if f"Unavailable: {item.get('metric')} (" not in text]
    stale = needs_stale_disclaimer(usable, ctx.start_ms)
    tail = [s for s in (rejected_sentence(claims), STALE_DISCLAIMER if stale else None) if s]
    notice = sample_notice(ctx)
    added = [s for s in middle + tail + ([notice] if notice else []) if s not in present and s not in excluded]
    if not added:
        return answer
    keep_tail = [s for s in tail if s in present or s in added]
    body = [s for s in answer.sentences if s not in keep_tail and s != notice]
    sentences = body + [s for s in middle if s in added] + keep_tail
    if notice and (notice in present or notice in added):
        sentences.append(notice)
    memory_ids = sorted(set(answer.memory_ids_cited) | {m for c in usable for code, m in c.caveat_memory_ids.items()
                                                        if code in missing_codes})
    kinds = {"conflicts": [k.id for k in conflicts if conflict_sentence(k, live_ids) in added],
             "caveats": [code for code in missing_codes if any(a.startswith(f"Caveat [{code}]:") for a in added)],
             "unavailable": [str(i.get("metric")) for i in unavailable if unavailable_sentence(i) in added],
             "rejected": rejected_sentence(claims) in added, "disclaimer": STALE_DISCLAIMER in added,
             "sample_notice": bool(notice and notice in added)}
    ctx.recover("writer", "narrative_completed",
                f"{len(added)} code-owned sentence(s) added after the last critic round "
                f"(conflicts: {', '.join(kinds['conflicts']) or 'none'}; caveats: {', '.join(kinds['caveats']) or 'none'}; "
                f"unavailable: {', '.join(kinds['unavailable']) or 'none'}; rejected list: {'yes' if kinds['rejected'] else 'no'})",
                sentences=[a[:160] for a in added], added=kinds)
    return Answer(verdict=answer.verdict, summary=list(answer.summary), sentences=sentences,
                  memory_ids_cited=memory_ids, source=answer.source if answer.source.endswith("+completed")
                  else answer.source + "+completed", round=answer.round, dropped=list(answer.dropped))


# ---------------------------------------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------------------------------------

def run_writer(ctx: "RunContext", claims: list[Claim], conflicts: list[Conflict], unavailable: list[dict[str, Any]],
               verdict: Verdict, issues: list[dict[str, Any]], round_no: int) -> AgentResult:
    notes: list[str] = []
    notice = sample_notice(ctx)
    stale = needs_stale_disclaimer([c for c in claims if c.status != "rejected"], ctx.start_ms)
    reinjection, memory_ids = "", []
    if round_no == 1:
        reinjection, memory_ids = retrieve_memories(ctx, ctx.inp.question)
        ctx.scratch["writer_memory_ids"] = memory_ids
    else:
        reinjection = ctx.scratch.get("writer_reinjection", "")
    ctx.scratch["writer_reinjection"] = reinjection
    answer: Optional[Answer] = None
    if ctx.generator is not None and ctx.generator.name != "null":
        answer = render_with_generator(ctx, claims, conflicts, unavailable, verdict, issues, reinjection, round_no)
        if answer is not None and stale and STALE_DISCLAIMER not in answer.sentences:
            answer.sentences.append(STALE_DISCLAIMER)
        rejected_text = rejected_sentence(claims)
        if answer is not None and rejected_text and rejected_text not in answer.sentences:
            answer.sentences.append(rejected_text)
        if answer is not None and notice and notice not in answer.sentences:
            answer.sentences.append(notice)
    if answer is None:
        answer = render_template(claims, conflicts, unavailable, verdict, notice, issues, stale, round_no)
        if ctx.generator is not None and ctx.generator.name != "null":
            notes.append("template renderer used after a generator fallback")
        if answer.dropped:
            ctx.recover("writer", "narrative_trimmed",
                        f"round {round_no}: {len(answer.dropped)} sentence(s) flagged by the Critic left out of the template",
                        sentences=[s[:160] for s in answer.dropped])
    log_rejected_revision(ctx, claims, round_no)
    status = "ok"
    return AgentResult("writer", status, answer, notes=notes)


def log_rejected_revision(ctx: "RunContext", claims: list[Claim], round_no: int) -> None:
    """A revision round that leaves out claims the Critic rejected since the last draft is a visible recovery."""
    seen: set[str] = ctx.scratch.setdefault("writer_rejected_seen", set())
    newly = [c for c in claims if c.status == "rejected" and c.id not in seen]
    if not newly:
        return
    seen.update(c.id for c in newly)
    ctx.recover("writer", "narrative_trimmed",
                f"round {round_no}: {len(newly)} claim(s) rejected by the Critic left out of the answer and named in "
                "its rejected-claims sentence",
                claims=[{"claim_id": c.id, "metric": c.metric, "rule": rejection_rule(c)} for c in newly])
