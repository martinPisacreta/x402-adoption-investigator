"""report.md renderer: sections, [cN] citations, conflicts section (SPEC.md tree, agents/report.py). Owner: B4.

Renders the final envelope (output.json content) as Markdown. Every number printed comes from a claim value, a
conflict value or a coverage field in the envelope; the renderer never computes anything.
"""
from __future__ import annotations

from typing import Any


def _fmt_value(value: Any) -> str:
    if isinstance(value, dict) and "share" in value:
        extra = {k: v for k, v in value.items() if k not in ("numerator", "denominator", "share")}
        text = f"{value.get('numerator')} / {value.get('denominator')} = {value.get('share')}"
        return text + (" (" + ", ".join(f"{k}={v}" for k, v in extra.items()) + ")" if extra else "")
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _evidence_cell(evidence: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for e in evidence:
        ref = e.get("ref", "")
        flags = []
        if e.get("replayed"):
            flags.append("replayed")
        if e.get("stale"):
            flags.append("stale")
        if e.get("verified"):
            flags.append("verified")
        sha = e.get("sha256")
        label = f"<{ref}>" if ref.startswith("http") else f"`{ref}`"
        if sha:
            label += f" sha256 {sha[:12]}…"
        if flags:
            label += " [" + ", ".join(flags) + "]"
        parts.append(label)
    return "<br>".join(parts) if parts else "none"


def _claims_table(claims: list[dict[str, Any]]) -> list[str]:
    lines = ["| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in claims:
        caveats = ", ".join(f"{code} ({c.get('caveat_memory_ids', {}).get(code, '-')})" for code in c.get("caveats", []))
        lines.append(f"| {c['id']} | {c['metric']} | {c.get('subject')} | {_fmt_value(c.get('value'))} | {c.get('unit')} | "
                     f"{c.get('confidence')} | {c.get('cross_checked')} | {caveats or '-'} | {', '.join(c.get('conflict_ids', [])) or '-'} | "
                     f"{_evidence_cell(c.get('evidence', []))} |")
    return lines


def _derivations(claims: list[dict[str, Any]], run_id: str = "") -> list[str]:
    lines: list[str] = []
    for c in claims:
        d = c.get("derivation") or {}
        cov = c.get("coverage")
        cov_text = (f"; coverage {cov.get('items_seen')} of {cov.get('total_reported')} ({cov.get('basis')})" if cov else "")
        log_ref = f"log://{run_id}#{d.get('log_seq')}" if d.get("log_seq") else "-"
        lines.append(f"- **{c['id']}** {c['statement']} — query `{d.get('query_id')}` on `{d.get('backend')}` "
                     f"(sql sha256 `{(d.get('sql_sha256') or '-')[:12]}`), reducer `{d.get('reducer')}`, {d.get('rows')} row(s), "
                     f"sources {', '.join(d.get('sources', []))}, fields {', '.join(d.get('fields', []))}{cov_text}; "
                     f"log `{log_ref}`")
        trail = c.get("critique", [])
        if trail:
            lines.append("  - critique: " + "; ".join(f"r{k.get('round')} {k.get('rule')} {k.get('verdict')}: {k.get('reason')}"
                                                     + (f" [{', '.join(k.get('memory_ids', []))}]" if k.get("memory_ids") else "")
                                                     for k in trail))
    return lines


def render_report(envelope: dict[str, Any]) -> str:
    run_id = envelope.get("run_id", "")
    status = envelope.get("status", "")
    lines: list[str] = [f"# x402 Adoption Investigator report: `{run_id}`", ""]
    if status == "error":
        err = envelope.get("error", {})
        lines += [f"**Status:** error `{err.get('code')}` at stage `{err.get('stage')}`", "", str(err.get("message", "")), "",
                  "```json", str(err.get("details", {})), "```", ""]
        return "\n".join(lines)
    out = envelope.get("output", {})
    mounts = out.get("mounts", {})
    lines += [f"**Status:** {status}  ",
              f"**Mode:** {mounts.get('mode')} (fetcher {mounts.get('fetcher')}, executor {mounts.get('executor')}, "
              f"generator {mounts.get('generator')}, fixtures captured {mounts.get('fixtures_captured_at')})", ""]
    for key in ("sample_mode_notice", "scaffold_notice"):
        if out.get(key):
            lines += [f"> {out[key]}", ""]
    lines += ["## Question", "", str(out.get("question", "")), ""]
    plan = out.get("plan", {})
    lines += ["## Plan", "", f"Playbook `{plan.get('playbook')}` (planner source: {plan.get('planner_source')}).  ",
              f"Sources: {', '.join(plan.get('sources', []))}.  ", f"Queries: {', '.join(plan.get('query_ids', []))}.  ",
              f"Memories retrieved by the Planner: {', '.join(plan.get('hmem_memory_ids', [])) or 'none'}.", ""]
    if plan.get("hypotheses"):
        lines += ["Hypotheses:", *[f"- {h}" for h in plan["hypotheses"]], ""]
    verdict = out.get("verdict", {})
    lines += ["## Verdict", "", f"**{verdict.get('label')}** (confidence {verdict.get('confidence')}; why: "
              f"{', '.join(verdict.get('why', [])) or 'none'})", ""]
    lines += ["## Answer", "", str(out.get("answer", "")), ""]
    lines += ["## Claims (accepted)", ""]
    claims = out.get("claims", [])
    if claims:
        lines += _claims_table(claims)
        lines += ["", "### Derivations and critique trail", ""]
        lines += _derivations(claims, run_id)
    else:
        lines.append("No accepted claim.")
    lines.append("")
    rejected = out.get("rejected_claims", [])
    lines += ["## Rejected claims", ""]
    if rejected:
        lines += _claims_table(rejected)
        lines += [""]
        lines += _derivations(rejected, run_id)
    else:
        lines.append("None.")
    lines.append("")
    lines += ["## Where sources disagree and why", ""]
    conflicts = out.get("conflicts", [])
    if conflicts:
        for k in conflicts:
            lines.append(f"### {k['id']}: {k['predicate']} ({k.get('subject')}) — {k.get('status')}")
            for v in k.get("values", []):
                lines.append(f"- {v.get('source')}: {_fmt_value(v.get('value'))} ({v.get('basis')}) <{v.get('url')}>")
            lines += ["", k.get("explanation", ""), f"Claims: {', '.join(k.get('claim_ids', [])) or 'none'}", ""]
    else:
        lines += ["None recorded.", ""]
    unavailable = out.get("unavailable", [])
    lines += ["## Unavailable", ""]
    lines += [f"- {u.get('metric')}: {u.get('reason')}" for u in unavailable] or ["None."]
    lines.append("")
    lines += ["## Verifications (Base)", ""]
    verifications = out.get("verifications", [])
    if verifications:
        lines += ["| tx | status | payer | seller | amount | block | facilitator_from | receipt match | reason |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for v in verifications:
            lines.append(f"| <{v.get('basescan_url')}> | {v.get('status')} | {v.get('payer')} | {v.get('seller')} | "
                         f"{v.get('amount_atomic')} | {v.get('block')} | {v.get('facilitator_from')} | {v.get('receipt_match')} | {v.get('reason') or '-'} |")
    else:
        lines.append("None.")
    lines.append("")
    lines += ["## Sources", ""]
    sources = out.get("sources", [])
    if sources:
        lines += ["| name | family | status | http | replayed | stale | sha256 | url |", "|---|---|---|---|---|---|---|---|"]
        for s in sources:
            lines.append(f"| {s.get('name')} | {s.get('family')} | {s.get('status')} | {s.get('http')} | {s.get('replayed')} | "
                         f"{s.get('stale')} | {(s.get('sha256') or '-')[:12]} | <{s.get('url')}> |")
    else:
        lines.append("No source fetched.")
    lines.append("")
    world = out.get("world", {})
    lines += ["## World Model (Meterless)", "", f"first pass: {world.get('first_pass')}  ", f"second pass: {world.get('second_pass')}  ",
              f"claims asserted: {world.get('claims_asserted')}, quarantined: {world.get('quarantined')}", ""]
    memory = out.get("memory", {})
    lines += ["## Memory (Meterless H-MEM)", "",
              f"retrieved: {len(memory.get('retrieved', []))}, cited: {', '.join(memory.get('cited', [])) or 'none'}, "
              f"feedback entries: {len(memory.get('feedback', []))}, mined: {memory.get('mined')}", ""]
    for fb in memory.get("feedback", []):
        lines.append(f"- {fb.get('memory_id')}: {fb.get('kind')} ({fb.get('reason')})")
    if memory.get("feedback"):
        lines.append("")
    lines += ["## Agents", "", "| role | generator-backed | generator used | attempts | status | notes |", "|---|---|---|---|---|---|"]
    for a in out.get("agents", []):
        lines.append(f"| {a.get('role')} | {a.get('generator_backed')} | {a.get('generator_used')} | {a.get('attempts')} | "
                     f"{a.get('status')} | {'; '.join(a.get('notes', [])) or '-'} |")
    calls = [(a.get("role"), c) for a in out.get("agents", []) for c in a.get("generator_calls", [])]
    if calls:
        lines += ["", "## Model calls", "",
                  "Which model handled each generator request (`generate` events in run.jsonl; Nightshift receipts).", "",
                  "| role | prompt | provider | requested | model that answered | route | receipt | recorded | log |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for role, c in sorted(calls, key=lambda rc: rc[1].get("log_seq") or 0):
            route = c.get("route") or "-"
            if c.get("route_kind"):
                route = f"{route} ({c['route_kind']})"
            lines.append(f"| {role} | {c.get('prompt_id')} | {c.get('provider')} | {c.get('requested_model') or '-'} | "
                         f"{c.get('model')} | {route} | {c.get('receipt_id') or '-'} | {c.get('recorded')} | "
                         f"log://{envelope.get('run_id')}#{c.get('log_seq')} |")
    lines += ["", f"Critique rounds: {out.get('critique_rounds')}. Log files: {', '.join(out.get('log_files', []))}.", ""]
    return "\n".join(lines)
