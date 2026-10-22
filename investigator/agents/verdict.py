"""Verdict rules: the label comes from accepted claims by rules, never from the model (SPEC.md section 6.8).

Labels: adoption_real | adoption_thin | self_settled_mostly | concentrated | diffuse | insufficient_evidence.
First match wins; `why` lists the claim ids used; confidence = min over the `why` claims' confidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..contracts import Claim


@dataclass
class Verdict:
    label: str
    confidence: float
    why: list[str] = field(default_factory=list)


def _share(value: Any) -> Optional[float]:
    if isinstance(value, dict) and value.get("share") is not None:
        return float(value["share"])
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("numerator"), (int, float)):
        return float(value["numerator"])
    return None


def _by_metric(claims: list[Claim]) -> dict[str, Claim]:
    found: dict[str, Claim] = {}
    for claim in claims:
        found.setdefault(claim.metric, claim)
    return found


def _result(label: str, used: list[Claim]) -> Verdict:
    confidence = min((c.confidence for c in used), default=0.0)
    return Verdict(label=label, confidence=round(confidence, 4), why=[c.id for c in used])


def compute_verdict(playbook: str, accepted: list[Claim]) -> Verdict:
    """Apply the rules of SPEC.md section 6.8 to the accepted claims only."""
    if len(accepted) < 3 or not any(c.cross_checked for c in accepted):
        return Verdict(label="insufficient_evidence", confidence=0.0, why=[])
    m = _by_metric(accepted)
    if playbook == "seller_profile":
        share = m.get("self_settlement_share")
        if share is not None and (_share(share.value) or 0.0) >= 0.5:
            return _result("self_settled_mostly", [share])
        payers = m.get("external_payers_total")
        repeat = m.get("repeat_external_payers")
        if payers is not None and repeat is not None and (_number(payers.value) or 0) >= 10 and (_number(repeat.value) or 0) >= 3:
            return _result("adoption_real", [payers, repeat])
        used = [c for c in (share, payers, repeat) if c is not None]
        return _result("adoption_thin", used or accepted[:1])
    if playbook == "catalog_concentration":
        top10 = m.get("top10_hosts_calls_share")
        if top10 is None:
            return Verdict(label="insufficient_evidence", confidence=0.0, why=[])
        label = "concentrated" if (_share(top10.value) or 0.0) >= 0.5 else "diffuse"
        return _result(label, [top10])
    if playbook == "payer_dossier":
        sales = m.get("payer_sales_total")
        verified = m.get("verified_tx")
        n_verified = _number(verified.value) if verified is not None else 0
        n_sales = _number(sales.value) if sales is not None else 0
        if (n_sales or 0) >= 2 and (n_verified or 0) >= 1:
            return _result("adoption_real", [c for c in (sales, verified) if c is not None])
        if (n_verified or 0) >= 1:
            return _result("adoption_thin", [c for c in (sales, verified) if c is not None])
        return Verdict(label="insufficient_evidence", confidence=0.0, why=[])
    return Verdict(label="insufficient_evidence", confidence=0.0, why=[])
