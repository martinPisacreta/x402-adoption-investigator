"""Settlement decoding: Transfer / AuthorizationUsed logs, transferWithAuthorization input, balanceOf calldata. Owner: B1.

Transfer topic0 -> payer = topics[1], seller = topics[2], amount = data. AuthorizationUsed topic0 -> payer =
topics[1]. Input selector 0xe3ee160e (transferWithAuthorization) -> from/to/value cross-check. tx.from is the
facilitator and is never taken as the payer (seed memory mem-seed-005).

Everything here is pure: no I/O, no network, no guessing. A log that does not belong to the token, has fewer
topics than the event needs, or carries malformed hex is skipped, never "repaired".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTHORIZATION_USED_TOPIC0 = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"
TRANSFER_WITH_AUTHORIZATION_SELECTOR = "0xe3ee160e"
BALANCE_OF_SELECTOR = "0x70a08231"
WORD_HEX = 64


@dataclass
class DecodedTransfer:
    token: str
    payer: str
    seller: str
    amount_atomic: int
    log_index: int
    decoded_from: str = "Transfer log"


def topic_address(topic: str) -> str:
    """32-byte topic -> 0x + 40 hex lowercase address."""
    return "0x" + topic[-40:].lower()


def balance_of_calldata(address: str) -> str:
    """ERC-20 balanceOf(address) calldata."""
    return BALANCE_OF_SELECTOR + address.lower().replace("0x", "").rjust(64, "0")


def hex_to_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None


def _same_address(a: Any, b: Any) -> bool:
    return str(a or "").lower() == str(b or "").lower()


def _topics(log: Any) -> list[str]:
    if not isinstance(log, dict):
        return []
    topics = log.get("topics") or []
    return [str(t).lower() for t in topics if isinstance(t, str)]


def _log_index(log: dict[str, Any], position: int) -> int:
    value = hex_to_int(log.get("logIndex")) if isinstance(log.get("logIndex"), str) else log.get("logIndex")
    return int(value) if isinstance(value, int) else position


def decode_transfer_logs(receipt: dict[str, Any], token: str) -> list[DecodedTransfer]:
    """Every Transfer log of `token` in the receipt, in log order (payer = topics[1], seller = topics[2])."""
    out: list[DecodedTransfer] = []
    for position, log in enumerate((receipt or {}).get("logs") or []):
        if not isinstance(log, dict) or not _same_address(log.get("address"), token):
            continue
        topics = _topics(log)
        if len(topics) < 3 or topics[0] != TRANSFER_TOPIC0:
            continue
        amount = hex_to_int(log.get("data"))
        if amount is None or len(topics[1]) < 42 or len(topics[2]) < 42:
            continue
        out.append(DecodedTransfer(token=str(log.get("address")).lower(), payer=topic_address(topics[1]),
                                   seller=topic_address(topics[2]), amount_atomic=amount,
                                   log_index=_log_index(log, position)))
    return out


def decode_authorization_used(receipt: dict[str, Any], token: str) -> Optional[str]:
    """Payer from the AuthorizationUsed log (topics[1]) when present."""
    for log in (receipt or {}).get("logs") or []:
        if not isinstance(log, dict) or not _same_address(log.get("address"), token):
            continue
        topics = _topics(log)
        if len(topics) >= 2 and topics[0] == AUTHORIZATION_USED_TOPIC0 and len(topics[1]) >= 42:
            return topic_address(topics[1])
    return None


def decode_transfer_with_authorization(input_hex: str) -> Optional[dict[str, Any]]:
    """{from, to, value, valid_after, valid_before, nonce} from a transferWithAuthorization input, else None."""
    if not isinstance(input_hex, str):
        return None
    data = input_hex.strip().lower()
    if not data.startswith(TRANSFER_WITH_AUTHORIZATION_SELECTOR) or len(data) < 10 + WORD_HEX * 3:
        return None
    body = data[10:]
    words = [body[i * WORD_HEX:(i + 1) * WORD_HEX] for i in range(len(body) // WORD_HEX)]
    try:
        value = int(words[2], 16)
        valid_after = int(words[3], 16) if len(words) > 3 else None
        valid_before = int(words[4], 16) if len(words) > 4 else None
    except ValueError:
        return None
    return {"from": "0x" + words[0][-40:], "to": "0x" + words[1][-40:], "value": value,
            "valid_after": valid_after, "valid_before": valid_before,
            "nonce": ("0x" + words[5]) if len(words) > 5 else None}


def block_time_iso(hex_seconds: Optional[str]) -> Optional[str]:
    """Hex unix seconds (eth blockTimestamp) -> ISO-8601 UTC with millisecond zeros, like the receipts `at` field."""
    import datetime as _dt
    seconds = hex_to_int(hex_seconds)
    if seconds is None:
        return None
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
