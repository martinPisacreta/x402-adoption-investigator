"""decoded tx -> onchain_tx, usdc_balance_atomic, tx_count facts (provenance kind chain). Owner: B2.

One JSON-RPC response per observation (a batch is answered element by element; a raw list produces nothing):
- eth_getTransactionReceipt  result.logs: Transfer (topic0 0xddf252ad...) -> payer topics[1], seller topics[2],
  amount data; AuthorizationUsed (topic0 0x98de5035...) -> payer topics[1]; result.from is the FACILITATOR
  (mem-seed-005, never the payer) -> fact onchain_tx about wallet:base:<payer>
- eth_getTransactionByHash   input selector 0xe3ee160e (transferWithAuthorization) -> from/to/value cross-check
  -> fact onchain_tx_input about wallet:base:<from> (kept separate so onchain_tx counts settlements only)
- eth_call balanceOf         hex word -> fact usdc_balance_atomic about the wallet named in the observation title
- eth_getTransactionCount    hex -> fact tx_count about the wallet named in the observation title
The wallet subject of balanceOf / nonce is not recoverable from the response, so the Collector names those
observations `rpc_balance_<0x...>` / `rpc_nonce_<0x...>` (INTEGRATION_NOTES.md); without a hint -> no events.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from ..domain import USDC_BASE, day_context_id, wallet_id
from ..types import AssertFact, IngestObservation, WriteEvent
from ._common import day_ctx, provenance, wallet_entity, wallet_hint

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTHORIZATION_USED_TOPIC = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
TRANSFER_WITH_AUTHORIZATION_SELECTOR = "0xe3ee160e"


def _hex_int(value: Any) -> Optional[int]:
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return None


def _topic_address(topic: Any) -> Optional[str]:
    if isinstance(topic, str) and len(topic) >= 42:
        return "0x" + topic[-40:].lower()
    return None


def _block_time(value: Any) -> Optional[str]:
    seconds = _hex_int(value)
    if seconds is None:
        return None
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def decode_receipt(result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Payer/seller/amount from the USDC Transfer log (fallback: payer from AuthorizationUsed); tx.from = facilitator."""
    tx = result.get("transactionHash")
    payer = seller = None
    amount = None
    decoded_from = None
    for log in result.get("logs") or []:
        if not isinstance(log, dict):
            continue
        topics = log.get("topics") or []
        if not topics:
            continue
        address = str(log.get("address") or "").lower()
        if topics[0] == TRANSFER_TOPIC and (address == USDC_BASE or not address) and len(topics) >= 3:
            payer, seller = _topic_address(topics[1]), _topic_address(topics[2])
            amount = _hex_int(log.get("data"))
            decoded_from = "Transfer log"
            break
    if payer is None:
        for log in result.get("logs") or []:
            topics = (log or {}).get("topics") or []
            if topics and topics[0] == AUTHORIZATION_USED_TOPIC and len(topics) >= 2:
                payer = _topic_address(topics[1])
                decoded_from = "AuthorizationUsed log"
                break
    if payer is None or not tx:
        return None
    return {"tx": tx, "payer": payer, "seller": seller, "amountAtomic": amount, "blockNumber": _hex_int(result.get("blockNumber")),
            "blockTime": _block_time(result.get("blockTimestamp") or next((l.get("blockTimestamp") for l in result.get("logs") or [] if isinstance(l, dict) and l.get("blockTimestamp")), None)),
            "facilitatorFrom": str(result.get("from") or "").lower() or None, "status": _hex_int(result.get("status")),
            "decodedFrom": decoded_from, "basescanUrl": f"https://basescan.org/tx/{tx}"}


def decode_transfer_with_authorization(result: dict[str, Any]) -> Optional[dict[str, Any]]:
    data = result.get("input")
    if not isinstance(data, str) or not data.startswith(TRANSFER_WITH_AUTHORIZATION_SELECTOR) or len(data) < 10 + 64 * 3:
        return None
    words = [data[10 + i * 64: 10 + (i + 1) * 64] for i in range(3)]
    return {"tx": result.get("hash"), "from": "0x" + words[0][-40:].lower(), "to": "0x" + words[1][-40:].lower(), "value": int(words[2], 16),
            "facilitatorFrom": str(result.get("from") or "").lower() or None, "chainId": _hex_int(result.get("chainId")),
            "nonce": _hex_int(result.get("nonce")), "blockNumber": _hex_int(result.get("blockNumber")),
            "decodedFrom": "input transferWithAuthorization"}


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """Payer and seller from the Transfer log; tx.from recorded as facilitator (mem-seed-005)."""
    payload = observation.payload
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    source = provenance(observation)
    title = (observation.title or "").lower()
    if isinstance(result, dict) and isinstance(result.get("logs"), list):
        decoded = decode_receipt(result)
        if decoded is None:
            return []
        at = decoded["blockTime"] or observation.at
        day = day_context_id(at)
        events: list[WriteEvent] = [day_ctx(at, source), wallet_entity(decoded["payer"], source, attrs={"role": "payer"})]
        if decoded["seller"]:
            events.append(wallet_entity(decoded["seller"], source, attrs={"role": "seller"}))
        events.append(AssertFact(about=wallet_id(decoded["payer"]), predicate="onchain_tx", value=decoded, source=source, confidence=0.95, context=day))
        return events
    if isinstance(result, dict) and isinstance(result.get("input"), str):
        decoded = decode_transfer_with_authorization(result)
        if decoded is None:
            return []
        day = day_context_id(observation.at)
        return [day_ctx(observation.at, source), wallet_entity(decoded["from"], source, attrs={"role": "payer"}),
                AssertFact(about=wallet_id(decoded["from"]), predicate="onchain_tx_input", value=decoded, source=source, confidence=0.9, context=day)]
    if isinstance(result, str) and result.startswith("0x"):
        wallet = wallet_hint(observation)
        value = _hex_int(result)
        if wallet is None or value is None:
            return []
        is_balance = "balance" in title or (len(result) == 66 and "nonce" not in title and "count" not in title)
        predicate = "usdc_balance_atomic" if is_balance else "tx_count"
        day = day_context_id(observation.at)
        return [day_ctx(observation.at, source), wallet_entity(wallet, source),
                AssertFact(about=wallet_id(wallet), predicate=predicate, value=value, source=source, confidence=0.95, context=day)]
    return []
