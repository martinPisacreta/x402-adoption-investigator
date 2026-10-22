"""Transfer / AuthorizationUsed / transferWithAuthorization decoding against the RPC fixtures (SPEC.md 6.4). Owner: B1."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.chain.base_rpc import USDC_BASE  # noqa: E402
from investigator.chain.decode import (AUTHORIZATION_USED_TOPIC0, TRANSFER_TOPIC0,  # noqa: E402
                                       TRANSFER_WITH_AUTHORIZATION_SELECTOR, balance_of_calldata, block_time_iso,
                                       decode_authorization_used, decode_transfer_logs,
                                       decode_transfer_with_authorization, topic_address)

FIXTURES = REPO_ROOT / "fixtures" / "raw"


class Constants(unittest.TestCase):
    def test_topics_and_selector(self) -> None:
        self.assertTrue(TRANSFER_TOPIC0.startswith("0xddf252ad"))
        self.assertTrue(AUTHORIZATION_USED_TOPIC0.startswith("0x98de5035"))
        self.assertEqual(TRANSFER_WITH_AUTHORIZATION_SELECTOR, "0xe3ee160e")

    def test_balance_of_calldata(self) -> None:
        data = balance_of_calldata("0x097e3F339D0b023605e12A6B81E2d6Cb7571475a")
        self.assertEqual(len(data), 2 + 8 + 64)
        self.assertTrue(data.startswith("0x70a08231000000000000000000000000097e3f339d0b"))

    def test_topic_address(self) -> None:
        self.assertEqual(topic_address("0x" + "0" * 24 + "097e3F339D0b023605e12A6B81E2d6Cb7571475a"),
                         "0x097e3f339d0b023605e12a6b81e2d6cb7571475a")

    def test_receipt_fixture_has_a_transfer_log(self) -> None:
        receipt = json.loads((FIXTURES / "base-rpc-eth_getTransactionReceipt.json").read_text(encoding="utf-8"))
        result = receipt.get("result", receipt)
        logs = result.get("logs", [])
        self.assertTrue(any((log.get("topics") or [""])[0].lower() == TRANSFER_TOPIC0 for log in logs))

    def test_decoding_matches_receipts_row(self) -> None:
        """Transfer log payer/seller/amount == the receipts row of the same tx; AuthorizationUsed and the
        transferWithAuthorization input agree; tx.from (the facilitator) is NOT the payer (mem-seed-005)."""
        receipt = json.loads((FIXTURES / "base-rpc-eth_getTransactionReceipt.json").read_text(encoding="utf-8"))["result"]
        tx = json.loads((FIXTURES / "base-rpc-eth_getTransactionByHash.json").read_text(encoding="utf-8"))["result"]
        receipts = json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))
        row = next(s for s in receipts["sales"] if s["transaction"].lower() == receipt["transactionHash"].lower())
        transfers = decode_transfer_logs(receipt, USDC_BASE)
        self.assertEqual(len(transfers), 1)
        transfer = transfers[0]
        self.assertEqual(transfer.payer, row["payer"].lower())
        self.assertEqual(transfer.seller, receipts["seller"].lower())
        self.assertEqual(transfer.amount_atomic, int(row["amountAtomic"]))
        self.assertEqual(transfer.decoded_from, "Transfer log")
        self.assertNotEqual(transfer.payer, receipt["from"].lower(), "tx.from is the facilitator, never the payer")
        self.assertEqual(decode_authorization_used(receipt, USDC_BASE), transfer.payer)
        decoded_input = decode_transfer_with_authorization(tx["input"])
        self.assertIsNotNone(decoded_input)
        self.assertEqual((decoded_input["from"], decoded_input["to"], decoded_input["value"]),
                         (transfer.payer, transfer.seller, transfer.amount_atomic))
        self.assertEqual(decode_transfer_logs(receipt, "0x" + "1" * 40), [], "another token has no Transfer log here")
        self.assertIsNone(decode_transfer_with_authorization("0xdeadbeef"))
        self.assertEqual(block_time_iso(tx["blockTimestamp"]), "2026-10-20T04:51:25.000Z")


if __name__ == "__main__":
    unittest.main()
