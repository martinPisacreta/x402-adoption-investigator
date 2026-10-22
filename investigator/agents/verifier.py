"""Verifier: chain verification sample, cross-registry checks, Conflict candidates (SPEC.md 6.4). Owner: B1 (landed by the integrator).

Tx sample: most recent external sale per distinct payer, newest first, capped at max_tx_verifications (the
focus wallet's sales first when set). The JSON-RPC calls go element by element through the mounted Fetcher
(chain/base_rpc.BaseRpcClient): eth_chainId probe, eth_getTransactionReceipt per sampled tx,
eth_getTransactionByHash only when the receipt decoded, eth_call balanceOf + eth_getTransactionCount for the
focus wallet (or the top external payer). Decoding in chain/decode.py: payer and seller come from the USDC
Transfer log, never from tx.from (recorded as facilitator_from; seed memory mem-seed-005). Receipt row must
match decoded payer, seller and amount -> verified; mismatch -> failed + Conflict settlement_<field>; RPC
unavailable -> unverifiable (chain metrics stay unavailable, never a zero); sample mode: only hashes present
in the fixtures are verifiable, the rest are unverifiable with reason fixture_miss, never guessed.

Cross-checks (one `crosscheck` event each): receipts seller == Bazaar payTo; registry endpoint counts
(cdp_search vs openapi / Agent402 / vet402 / x402-trust); Bazaar calls_30d summed over the host's listings vs
receipts in the same 30-day window (expected to disagree: different bases). Writes exports/settlements.json only
when at least one receipt was decoded, so an unreachable chain never becomes a settlements view with zero rows.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..chain import base_rpc
from ..chain.base_rpc import BASE_CHAIN_ID, BASE_RPC_URL, USDC_BASE, BaseRpcClient, result_of
from ..chain.decode import (block_time_iso, decode_authorization_used, decode_transfer_logs,
                            decode_transfer_with_authorization, hex_to_int)
from ..contracts import AgentResult, Conflict, ConflictValue, Observation, Plan, Verification
from . import collector as collector_mod
from .metrics import REGISTRY_BASIS
from .reducers import window_since

if TYPE_CHECKING:
    from ..runner import RunContext

BASESCAN_TX = "https://basescan.org/tx/{tx}"
SETTLEMENTS_EXPORT = "settlements"
TRANSFER_BASIS = "USDC Transfer log of the on-chain receipt (eth_getTransactionReceipt on Base)"
RECEIPTS_BASIS = "the seller's own receipts.json row for the transaction"
CONFLICT_EXPLANATION_SETTLEMENT = ("The seller's receipt row and the decoded USDC Transfer log disagree; the chain is "
                                   "authoritative for payer, seller and amount, the receipt is self-reported.")
CONFLICT_EXPLANATION_SELLER = ("The wallet the receipts name as seller is not among the payTo wallets of the host's "
                               "Bazaar listings; either the store changed wallets or the listing is stale.")


def empty_payload() -> dict[str, Any]:
    return {"verifications": [], "crosschecks": [], "conflicts": [], "chain_facts": [], "settlements": [],
            "observations": []}


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------------------------------------
# Tx sample
# ----------------------------------------------------------------------------------------------------------

def select_tx_sample(sales: list[dict[str, Any]], focus_wallet: Optional[str], cap: int) -> list[dict[str, Any]]:
    """Deterministic sample (SPEC.md 6.4): the focus wallet's sales first (newest first), then the most recent
    external sale per distinct payer, newest first, capped at `cap`. Rows without a transaction hash are skipped."""
    limit = max(0, int(cap or 0))
    focus = _lower(focus_wallet) or None
    rows = [s for s in (sales or []) if isinstance(s, dict) and s.get("transaction")]
    newest_first = sorted(rows, key=lambda s: (str(s.get("at") or ""), _lower(s.get("transaction"))), reverse=True)
    sample: list[dict[str, Any]] = []
    seen: set[str] = set()
    if focus:
        for sale in newest_first:
            if len(sample) >= limit:
                break
            if _lower(sale.get("payer")) == focus:
                sample.append(sale)
                seen.add(focus)
    for sale in newest_first:
        if len(sample) >= limit:
            break
        if sale.get("ownWallet"):
            continue
        payer = _lower(sale.get("payer"))
        if not payer or payer in seen:
            continue
        seen.add(payer)
        sample.append(sale)
    return sample


def top_external_payer(sales: list[dict[str, Any]]) -> Optional[str]:
    """The external payer with the most sales (ties: the most recent sale, then the lowest address)."""
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for sale in sales or []:
        if not isinstance(sale, dict) or sale.get("ownWallet"):
            continue
        payer = _lower(sale.get("payer"))
        if not payer:
            continue
        counts[payer] = counts.get(payer, 0) + 1
        latest[payer] = max(latest.get(payer, ""), str(sale.get("at") or ""))
    if not counts:
        return None
    return sorted(counts, key=lambda p: (-counts[p], latest[p], p))[0]


# ----------------------------------------------------------------------------------------------------------
# One tx
# ----------------------------------------------------------------------------------------------------------

def _unverifiable(tx_hash: str, reason: str) -> Verification:
    return Verification(tx=tx_hash, status="unverifiable", payer=None, seller=None, amount_atomic=None, block=None,
                        block_time=None, facilitator_from=None, basescan_url=BASESCAN_TX.format(tx=tx_hash),
                        decoded_from="none", receipt_match=None, reason=reason)


def _first_log_timestamp(receipt: dict[str, Any]) -> Optional[str]:
    for log in receipt.get("logs") or []:
        if isinstance(log, dict) and log.get("blockTimestamp"):
            return str(log["blockTimestamp"])
    return None


def verify_tx(ctx: "RunContext", sale: dict[str, Any], receipt: Optional[dict[str, Any]],
              tx: Optional[dict[str, Any]], *, seller: Optional[str] = None, unavailable_reason: Optional[str] = None,
              replayed: bool = False, stale: bool = False) -> Verification:
    """Decode the receipt logs (and cross-check the tx input) against the receipts row; emit the `verify` event."""
    tx_hash = _lower(sale.get("transaction"))
    expected_payer = _lower(sale.get("payer"))
    expected_seller = _lower(seller)
    expected_amount = _int(sale.get("amountAtomic"))
    input_match: Optional[bool] = None
    if receipt is None:
        verification = _unverifiable(tx_hash, unavailable_reason or "rpc_unavailable")
    else:
        transfers = decode_transfer_logs(receipt, USDC_BASE)
        chosen = next((t for t in transfers if t.seller == expected_seller and t.payer == expected_payer), None) \
            or next((t for t in transfers if t.seller == expected_seller), None) \
            or (transfers[0] if transfers else None)
        block = hex_to_int(receipt.get("blockNumber"))
        block_time = block_time_iso((tx or {}).get("blockTimestamp") or receipt.get("blockTimestamp") or _first_log_timestamp(receipt))
        facilitator = _lower(receipt.get("from") or (tx or {}).get("from")) or None
        basescan = BASESCAN_TX.format(tx=tx_hash)
        if chosen is None:
            payer = decode_authorization_used(receipt, USDC_BASE)
            verification = Verification(tx=tx_hash, status="failed", payer=payer, seller=None, amount_atomic=None, block=block,
                                        block_time=block_time, facilitator_from=facilitator, basescan_url=basescan,
                                        decoded_from="AuthorizationUsed log" if payer else "none", receipt_match=False,
                                        reason="decode_mismatch:transfer_log")
        else:
            mismatches: list[str] = []
            if chosen.payer != expected_payer:
                mismatches.append("payer")
            if chosen.seller != expected_seller:
                mismatches.append("seller")
            if expected_amount is None or chosen.amount_atomic != expected_amount:
                mismatches.append("amount")
            if hex_to_int(receipt.get("status")) != 1:
                mismatches.append("status")
            match = not mismatches
            verification = Verification(tx=tx_hash, status="verified" if match else "failed", payer=chosen.payer,
                                         seller=chosen.seller, amount_atomic=chosen.amount_atomic, block=block,
                                         block_time=block_time, facilitator_from=facilitator, basescan_url=basescan,
                                         decoded_from=chosen.decoded_from, receipt_match=match,
                                         reason=None if match else f"decode_mismatch:{mismatches[0]}")
            if tx and isinstance(tx.get("input"), str):
                decoded_input = decode_transfer_with_authorization(tx["input"])
                if decoded_input is not None:
                    input_match = (decoded_input["from"] == chosen.payer and decoded_input["to"] == chosen.seller
                                   and decoded_input["value"] == chosen.amount_atomic)
    ctx.log.emit("verify", "verifier", {
        "tx": verification.tx, "status": verification.status, "payer": verification.payer, "seller": verification.seller,
        "amount_atomic": verification.amount_atomic, "basescan_url": verification.basescan_url,
        "receipt_match": verification.receipt_match, "reason": verification.reason, "block": verification.block,
        "block_time": verification.block_time, "facilitator_from": verification.facilitator_from,
        "decoded_from": verification.decoded_from, "input_match": input_match, "receipt_payer": expected_payer or None,
        "receipt_amount_atomic": expected_amount, "replayed": replayed, "stale": stale})
    return verification


def settlement_conflict(verification: Verification, sale: dict[str, Any], seller: str, receipts_url: str,
                        captured_at: Optional[str]) -> Conflict:
    field = (verification.reason or "decode_mismatch:unknown").split(":", 1)[1]
    chain_value: Any = {"payer": verification.payer, "seller": verification.seller, "amount": verification.amount_atomic}.get(field)
    receipt_value: Any = {"payer": _lower(sale.get("payer")), "seller": _lower(seller), "amount": _int(sale.get("amountAtomic"))}.get(field)
    if field == "status":
        chain_value, receipt_value = "reverted", "settled"
    if field == "transfer_log":
        chain_value, receipt_value = "no USDC Transfer log in the receipt", "settled sale"
    return Conflict(id="k00", predicate=f"settlement_{field}", subject=f"tx:base:{verification.tx}",
                    values=[ConflictValue(source="base_rpc", value=chain_value, url=verification.basescan_url, basis=TRANSFER_BASIS),
                            ConflictValue(source="receipts", value=receipt_value, url=receipts_url, basis=RECEIPTS_BASIS,
                                          captured_at=captured_at)],
                    explanation=CONFLICT_EXPLANATION_SETTLEMENT, status="open", claim_ids=[])


# ----------------------------------------------------------------------------------------------------------
# Cross-checks
# ----------------------------------------------------------------------------------------------------------

def _named(observations: list[Observation], name: Optional[str], family: Optional[str] = None) -> Optional[Observation]:
    """The ok observation called `name`, else the first ok observation of `family`."""
    if name:
        for obs in observations:
            if obs.status == "ok" and obs.name == name:
                return obs
    if family:
        for obs in observations:
            if obs.status == "ok" and obs.family == family:
                return obs
    return None


def _crosscheck(ctx: "RunContext", name: str, a: dict[str, Any], b: dict[str, Any], agree: bool, note: str,
                out: list[dict[str, Any]]) -> None:
    payload = {"name": name, "a": a, "b": b, "agree": agree, "note": note}
    seq = ctx.log.emit("crosscheck", "verifier", payload)
    out.append({"seq": seq, **payload})


def cross_checks(ctx: "RunContext", observations: list[Observation], host: Optional[str] = None,
                 since: Optional[str] = None) -> tuple[list[dict[str, Any]], list[Conflict]]:
    """Emit one `crosscheck` event per comparison and return (crosschecks, conflict candidates)."""
    checks: list[dict[str, Any]] = []
    conflicts: list[Conflict] = []
    receipts_obs = _named(observations, "store_receipts", "receipts")
    search_obs = _named(observations, "cdp_search_host", "cdp_search")
    receipts = receipts_obs.payload if receipts_obs and isinstance(receipts_obs.payload, dict) else None
    search = search_obs.payload if search_obs and isinstance(search_obs.payload, dict) else None
    resources = [r for r in (search or {}).get("resources") or [] if isinstance(r, dict)]
    seller = _lower((receipts or {}).get("seller")) or None
    host_label = host or "?"

    if receipts and search:
        pay_tos = sorted({_lower(a.get("payTo")) for r in resources for a in (r.get("accepts") or []) if isinstance(a, dict) and a.get("payTo")})
        agree = bool(seller) and seller in pay_tos
        _crosscheck(ctx, "receipts_seller_vs_bazaar_payto",
                    {"source": "receipts", "value": seller, "url": receipts_obs.url},
                    {"source": "cdp_search", "value": pay_tos, "url": search_obs.url}, agree,
                    "the wallet the receipts name as seller should be a payTo of the host's Bazaar listings", checks)
        if not agree:
            conflicts.append(Conflict(id="k00", predicate="seller_payto_mismatch", subject=f"seller:host:{host_label}",
                                      values=[ConflictValue(source="receipts", value=seller, url=receipts_obs.url, basis=RECEIPTS_BASIS,
                                                            captured_at=receipts_obs.captured_at),
                                              ConflictValue(source="cdp_search", value=pay_tos, url=search_obs.url,
                                                            basis=REGISTRY_BASIS["cdp_search"], captured_at=search_obs.captured_at)],
                                      explanation=CONFLICT_EXPLANATION_SELLER, status="open", claim_ids=[]))

    if search:
        bazaar_count = len(resources)
        a = {"source": "cdp_search", "value": bazaar_count, "url": search_obs.url}
        others: list[tuple[str, Any, Observation]] = []
        openapi_obs = _named(observations, "store_openapi", "store_openapi")
        if openapi_obs and isinstance(openapi_obs.payload, dict):
            others.append(("store_openapi", len(collector_mod.flatten_openapi_paid_ops(openapi_obs.payload)), openapi_obs))
        agent_obs = _named(observations, "agent402_index")
        if agent_obs and isinstance(agent_obs.payload, dict) and agent_obs.payload.get("paidToolCount") is not None:
            others.append(("agent402", {"paidToolCount": agent_obs.payload.get("paidToolCount"),
                                        "toolCount": agent_obs.payload.get("toolCount")}, agent_obs))
        vet_obs = _named(observations, "vet402_resolve", "vet402")
        if vet_obs and isinstance(vet_obs.payload, dict):
            others.append(("vet402", len(vet_obs.payload.get("endpoints") or []), vet_obs))
        trust_obs = _named(observations, "trust_search")
        if trust_obs and isinstance(trust_obs.payload, dict) and trust_obs.payload.get("total") is not None:
            others.append(("x402_trust", trust_obs.payload.get("total"), trust_obs))
        for source, value, obs in others:
            comparable = value.get("paidToolCount") if isinstance(value, dict) else value
            _crosscheck(ctx, f"endpoint_count_{source}_vs_cdp_search", a, {"source": source, "value": value, "url": obs.url},
                        comparable == bazaar_count, f"{REGISTRY_BASIS['cdp_search']} vs {REGISTRY_BASIS[source]}", checks)

    if receipts and search:
        window_start = since or window_since(ctx.start_iso)
        calls = sum(_int((r.get("quality") or {}).get("l30DaysTotalCalls")) or 0 for r in resources)
        sales_30d = sum(1 for s in receipts.get("sales") or [] if isinstance(s, dict) and str(s.get("at") or "") >= window_start)
        _crosscheck(ctx, "bazaar_calls_30d_vs_receipts_30d",
                    {"source": "cdp_search", "value": calls, "url": search_obs.url},
                    {"source": "receipts", "value": sales_30d, "url": receipts_obs.url}, calls == sales_30d,
                    f"facilitator per-endpoint 30-day counters (search capped at twenty) vs receipts rows settled since "
                    f"{window_start} (own-wallet rows included); different bases, expected to disagree", checks)
    return checks, conflicts


# ----------------------------------------------------------------------------------------------------------
# Exports and facts
# ----------------------------------------------------------------------------------------------------------

def settlement_rows(verifications: list[Verification]) -> list[dict[str, Any]]:
    rows = [{"tx_hash": v.tx, "payer": v.payer, "seller": v.seller, "amount_atomic": v.amount_atomic,
             "block_number": v.block, "block_time": v.block_time, "facilitator_from": v.facilitator_from, "status": v.status}
            for v in verifications if v.status in ("verified", "failed")]
    return sorted(rows, key=lambda r: r["tx_hash"])


def write_settlements_export(ctx: "RunContext", verifications: list[Verification]) -> Path:
    """exports/settlements.json rows: tx_hash, payer, seller, amount_atomic, block_number, block_time, facilitator_from, status."""
    path = ctx.exports_dir / f"{SETTLEMENTS_EXPORT}.json"
    path.write_text(json.dumps(settlement_rows(verifications), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8")
    return path


def chain_fact(about: str, predicate: str, value: Any, obs: Observation, *, url: Optional[str] = None,
               at: Optional[str] = None, confidence: float = 0.95) -> dict[str, Any]:
    """Keeper-shaped chain fact {about, predicate, value, url, at, checksum, kind, by} plus the `source` dict the
    Analyst's evidence builder reads (same url / checksum / captured_at / replayed)."""
    checksum = f"sha256:{obs.bytes_sha256}" if obs.bytes_sha256 else None
    fact_url = url or obs.url
    fact_at = at or obs.captured_at
    source = {"kind": "chain", "url": fact_url, "checksum": checksum, "at": fact_at, "replayed": obs.replayed,
              "stale": obs.stale, "fixture_file": obs.fixture_file, "request_key": obs.request_key}
    return {"about": about, "predicate": predicate, "value": value, "url": fact_url, "at": fact_at, "checksum": checksum,
            "kind": "chain", "by": "verifier", "confidence": confidence, "source": source}


# ----------------------------------------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------------------------------------

def _reason_for(obs: Observation) -> str:
    if obs.status == "fixture_miss":
        return "fixture_miss"
    if obs.status == "unavailable":
        return "budget_exhausted" if "budget_exhausted" in (obs.error or "") else "rpc_unavailable"
    if obs.error:
        return "rpc_error"
    return "tx_not_found"


def run_verifier(ctx: "RunContext", plan: Plan, observations: list[Observation]) -> AgentResult:
    payload = empty_payload()
    receipts_obs = _named(observations, "store_receipts", "receipts")
    if receipts_obs is None or not isinstance(receipts_obs.payload, dict):
        crosschecks, conflicts = cross_checks(ctx, observations, host=plan.params.get("host"))
        payload["crosschecks"], payload["conflicts"] = crosschecks, conflicts
        return AgentResult("verifier", "ok", payload, notes=[
            "no receipts observation in this plan: no settlement to sample, no RPC call made"])
    receipts = receipts_obs.payload
    seller = _lower(receipts.get("seller"))
    sales = [s for s in receipts.get("sales") or [] if isinstance(s, dict)]
    focus_wallet = _lower(plan.params.get("wallet")) or None
    sample = select_tx_sample(sales, focus_wallet, ctx.budget.max_tx_verifications)
    client = BaseRpcClient(ctx.fetcher, observer=lambda obs: collector_mod.record_observation(ctx, obs),
                           budget=lambda: ctx.budget_take("fetches", ctx.budget.max_fetches, "verifier"))
    live = ctx.mounts.fetcher != "fixture"
    notes: list[str] = []

    probe = client.batch([base_rpc.call_chain_id()])[0]
    chain_id = hex_to_int(result_of(probe)) if probe.status == "ok" else None
    rpc_down = live and probe.status != "ok"
    wrong_chain = chain_id is not None and chain_id != BASE_CHAIN_ID
    if rpc_down:
        ctx.recover("verifier", "rpc_unavailable", f"Base RPC probe failed ({probe.error}); settlements stay unverified",
                    url=BASE_RPC_URL, status=probe.status)
    if wrong_chain:
        ctx.recover("verifier", "rpc_unavailable", f"RPC answered chain id {chain_id}, expected {BASE_CHAIN_ID}; settlements stay unverified",
                    url=BASE_RPC_URL)

    verifications: list[Verification] = []
    conflicts: list[Conflict] = []
    decoded_receipts: dict[str, Observation] = {}
    deadline_skipped = 0
    for sale in sample:
        tx_hash = _lower(sale.get("transaction"))
        if not ctx.budget_take("tx_verifications", ctx.budget.max_tx_verifications, "verifier"):
            break
        if ctx.deadline_hit("verifier"):
            ctx.recover("verifier", "deadline_skip", f"{tx_hash}: chain verification skipped: deadline", tx=tx_hash)
            deadline_skipped += 1
            verifications.append(verify_tx(ctx, sale, None, None, seller=seller, unavailable_reason="deadline_skip"))
            continue
        if rpc_down or wrong_chain:
            verifications.append(verify_tx(ctx, sale, None, None, seller=seller, unavailable_reason="rpc_unavailable"))
            continue
        receipt_obs = client.batch([base_rpc.call_receipt(tx_hash)])[0]
        receipt = result_of(receipt_obs)
        if not isinstance(receipt, dict):
            verifications.append(verify_tx(ctx, sale, None, None, seller=seller, unavailable_reason=_reason_for(receipt_obs),
                                           replayed=receipt_obs.replayed, stale=receipt_obs.stale))
            continue
        tx_obs = client.batch([base_rpc.call_transaction(tx_hash)])[0]
        tx = result_of(tx_obs)
        verification = verify_tx(ctx, sale, receipt, tx if isinstance(tx, dict) else None, seller=seller,
                                 replayed=receipt_obs.replayed, stale=receipt_obs.stale)
        decoded_receipts[tx_hash] = receipt_obs
        if verification.status == "failed":
            conflicts.append(settlement_conflict(verification, sale, seller, receipts_obs.url, receipts_obs.captured_at))
        verifications.append(verification)

    chain_facts: list[dict[str, Any]] = []
    for verification in verifications:
        if verification.status != "verified" or not verification.payer:
            continue
        obs = decoded_receipts.get(verification.tx)
        if obs is None:
            continue
        value = {"tx": verification.tx, "payer": verification.payer, "seller": verification.seller,
                 "amountAtomic": verification.amount_atomic, "blockNumber": verification.block, "blockTime": verification.block_time,
                 "facilitatorFrom": verification.facilitator_from, "decodedFrom": verification.decoded_from,
                 "basescanUrl": verification.basescan_url, "receiptMatch": verification.receipt_match}
        chain_facts.append(chain_fact(f"wallet:base:{verification.payer}", "onchain_tx", value, obs,
                                      url=verification.basescan_url, at=verification.block_time or obs.captured_at))

    wallet = focus_wallet or top_external_payer(sales)
    if wallet and not rpc_down and not wrong_chain and not ctx.deadline_hit("verifier"):
        balance_obs = client.batch([base_rpc.call_balance_of(wallet)])[0]
        nonce_obs = client.batch([base_rpc.call_nonce(wallet)])[0]
        for obs, predicate in ((balance_obs, "usdc_balance_atomic"), (nonce_obs, "tx_count")):
            value = hex_to_int(result_of(obs)) if obs.status == "ok" else None
            if value is None:
                notes.append(f"{predicate} for {wallet} not available: {_reason_for(obs)}")
                continue
            chain_facts.append(chain_fact(f"wallet:base:{wallet}", predicate, value, obs))

    crosschecks, xconflicts = cross_checks(ctx, observations, host=plan.params.get("host"))
    conflicts.extend(xconflicts)
    for index, conflict in enumerate(conflicts, 1):
        conflict.id = f"k{index:02d}"
        ctx.log.emit("conflict", "verifier", {
            "conflict_id": conflict.id, "predicate": conflict.predicate, "subject": conflict.subject,
            "values": [{"source": v.source, "value": v.value, "url": v.url, "basis": v.basis} for v in conflict.values],
            "explanation": conflict.explanation, "status": conflict.status, "claim_ids": []})

    settlements: list[dict[str, Any]] = []
    if decoded_receipts:
        write_settlements_export(ctx, verifications)
        settlements = settlement_rows(verifications)

    counts = {"verified": 0, "failed": 0, "unverifiable": 0}
    reasons: dict[str, int] = {}
    for v in verifications:
        counts[v.status] = counts.get(v.status, 0) + 1
        if v.status == "unverifiable" and v.reason:
            reasons[v.reason] = reasons.get(v.reason, 0) + 1
    reason_text = ", ".join(f"{k} x{n}" for k, n in sorted(reasons.items())) or "none"
    export_note = "yes" if decoded_receipts else "no"
    notes.insert(0, f"{len(sample)} tx sampled ({plan.tx_sample_rule}): {counts['verified']} verified, {counts['failed']} failed, "
                    f"{counts['unverifiable']} unverifiable ({reason_text}); chain facts: {len(chain_facts)}; "
                    f"cross-checks: {len(crosschecks)}; settlements export: {export_note}")
    status = "degraded" if (rpc_down or wrong_chain or deadline_skipped) else "ok"
    payload.update({"verifications": verifications, "crosschecks": crosschecks, "conflicts": conflicts,
                    "chain_facts": chain_facts, "settlements": settlements, "observations": list(client.observations)})
    return AgentResult("verifier", status, payload, notes=notes)
