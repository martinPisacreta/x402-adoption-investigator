"""JSON-RPC client for Base, fixture-backed in sample mode (SPEC.md 6.4 and 7.1). Owner: B1.

The Verifier builds one logical batch per run: eth_getTransactionReceipt x k, eth_getTransactionByHash x k,
eth_call balanceOf(payer), eth_getTransactionCount(payer) (+ eth_blockNumber / eth_chainId). The batch is sent
element by element through the mounted Fetcher (one POST per element in live mode, one fixture per element in
sample mode), each with its own request key rpc_request_key(method, params), its own sha256 and its own
`fetch` event, so a failing element is visible on its own and fixtures (single responses) serve it unchanged.
RPC unavailable -> the caller logs recovery rpc_unavailable and omits chain claims; nothing is guessed.

The call builders here are the ONLY place JSON-RPC params are shaped (hashes and addresses lowercased), and
scripts/build_manifest_requests.py derives the fixture request keys from these same builders.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..contracts import Fetcher, Observation
from ..sources.registry import rpc_request_key
from .decode import balance_of_calldata

BASE_RPC_URL = "https://mainnet.base.org"
BASE_CHAIN_ID = 8453
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


def _hex(value: str) -> str:
    text = str(value).strip().lower()
    return text if text.startswith("0x") else "0x" + text


def rpc_call(method: str, params: list[Any], call_id: int) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}


# -- call builders (method + params; the client assigns ids) -------------------------------------------------

def call_receipt(tx_hash: str) -> dict[str, Any]:
    return {"method": "eth_getTransactionReceipt", "params": [_hex(tx_hash)]}


def call_transaction(tx_hash: str) -> dict[str, Any]:
    return {"method": "eth_getTransactionByHash", "params": [_hex(tx_hash)]}


def call_balance_of(address: str, token: str = USDC_BASE) -> dict[str, Any]:
    return {"method": "eth_call", "params": [{"to": _hex(token), "data": balance_of_calldata(address)}, "latest"]}


def call_nonce(address: str) -> dict[str, Any]:
    return {"method": "eth_getTransactionCount", "params": [_hex(address), "latest"]}


def call_block_number() -> dict[str, Any]:
    return {"method": "eth_blockNumber", "params": []}


def call_chain_id() -> dict[str, Any]:
    return {"method": "eth_chainId", "params": []}


def settlement_batch(tx_hashes: list[str], payer: Optional[str], token: str = USDC_BASE,
                     with_chain_header: bool = True) -> list[dict[str, Any]]:
    """The standard per-run batch: receipts x k, txs x k, balanceOf(payer), nonce(payer), blockNumber, chainId."""
    calls: list[dict[str, Any]] = [call_receipt(h) for h in tx_hashes] + [call_transaction(h) for h in tx_hashes]
    if payer:
        calls.append(call_balance_of(payer, token))
        calls.append(call_nonce(payer))
    if with_chain_header:
        calls.append(call_block_number())
        calls.append(call_chain_id())
    return calls


def observation_name(method: str, params: list[Any]) -> str:
    """rpc_receipt_<hash> | rpc_tx_<hash> | rpc_balance_<addr> | rpc_nonce_<addr> | rpc_block_number | rpc_chain_id."""
    if method == "eth_getTransactionReceipt":
        return f"rpc_receipt_{params[0]}"
    if method == "eth_getTransactionByHash":
        return f"rpc_tx_{params[0]}"
    if method == "eth_call":
        data = str((params[0] or {}).get("data", "")) if params and isinstance(params[0], dict) else ""
        return f"rpc_balance_0x{data[-40:]}" if len(data) >= 40 else "rpc_call"
    if method == "eth_getTransactionCount":
        return f"rpc_nonce_{params[0]}"
    if method == "eth_blockNumber":
        return "rpc_block_number"
    if method == "eth_chainId":
        return "rpc_chain_id"
    return f"rpc_{method}"


def request_key_for(method: str, params: list[Any]) -> str:
    return rpc_request_key(method, params)


def result_of(obs: Observation) -> Optional[Any]:
    """The JSON-RPC `result` of an ok Observation without an `error`, else None."""
    if obs.status != "ok" or not isinstance(obs.payload, dict):
        return None
    if obs.payload.get("error"):
        return None
    return obs.payload.get("result")


def hex_to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value), 16)
    except ValueError:
        return None


class BaseRpcClient:
    def __init__(self, fetcher: Fetcher, url: str = BASE_RPC_URL, timeout: float = 10.0,
                 observer: Optional[Callable[[Observation], None]] = None,
                 budget: Optional[Callable[[], bool]] = None) -> None:
        """observer(obs) is called per element (the Verifier logs the fetch event there); budget() returns
        False when no fetch may be made any more (the element is then reported unavailable, never guessed)."""
        self.fetcher = fetcher
        self.url = url
        self.timeout = timeout
        self.observer = observer
        self.budget = budget
        self._next_id = 1
        self.observations: list[Observation] = []

    def batch(self, calls: list[dict[str, Any]]) -> list[Observation]:
        """One Observation per element, in order (request_key = rpc_request_key(method, params))."""
        out: list[Observation] = []
        for call in calls:
            method = call["method"]
            params = list(call.get("params") or [])
            key = rpc_request_key(method, params)
            body = rpc_call(method, params, self._next_id)
            self._next_id += 1
            if self.budget is not None and not self.budget():
                obs = Observation(name=observation_name(method, params), family="base_rpc", request_key=key,
                                  url=self.url, method="POST", http=None, bytes_sha256=None, latency_ms=0,
                                  captured_at="", replayed=False, stale=False, status="unavailable",
                                  error="budget_exhausted: max_fetches reached", payload=None)
            else:
                try:
                    obs = self.fetcher.fetch(key, self.url, "POST", body, self.timeout)
                except Exception as exc:  # FetchError from a bare LiveFetcher; the guard error is re-raised
                    if type(exc).__name__ == "NetworkDisabledError":
                        raise
                    obs = Observation(name=observation_name(method, params), family="base_rpc", request_key=key,
                                      url=self.url, method="POST", http=getattr(exc, "http", None), bytes_sha256=None,
                                      latency_ms=0, captured_at="", replayed=False, stale=False,
                                      status="unavailable", error=str(exc), payload=None)
            obs.name = observation_name(method, params)
            obs.family = "base_rpc"
            if obs.status == "ok" and isinstance(obs.payload, dict) and obs.payload.get("error"):
                obs.error = f"rpc error: {obs.payload.get('error')}"
            out.append(obs)
            self.observations.append(obs)
            if self.observer:
                self.observer(obs)
        return out

    def _single(self, call: dict[str, Any]) -> Optional[Any]:
        return result_of(self.batch([call])[0])

    def receipt(self, tx_hash: str) -> Optional[dict[str, Any]]:
        result = self._single(call_receipt(tx_hash))
        return result if isinstance(result, dict) else None

    def transaction(self, tx_hash: str) -> Optional[dict[str, Any]]:
        result = self._single(call_transaction(tx_hash))
        return result if isinstance(result, dict) else None

    def usdc_balance(self, address: str) -> Optional[int]:
        return hex_to_int(self._single(call_balance_of(address)))

    def nonce(self, address: str) -> Optional[int]:
        return hex_to_int(self._single(call_nonce(address)))

    def block_number(self) -> Optional[int]:
        return hex_to_int(self._single(call_block_number()))

    def chain_id(self) -> Optional[int]:
        return hex_to_int(self._single(call_chain_id()))
