"""Every Nightshift receipt the repository cites is in Nightshift's own signed ledger, and the signature holds.

examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl is an unedited excerpt of the runtime's
<stateDir>/receipts.jsonl (scripts/nightshift_ledger.py --copy-to). Each line is an envelope {body, sig_b64,
pubkey_b64} signed by Nightshift's daemon with Ed25519. These tests check, offline: the verifier against the RFC 8032
test vectors; that every receipt id named by a committed live trace (examples/live-trace-nightshift/ and
examples/live-trace-zetaris-nightshift/), a recorded transcript or a committed example is in the excerpt with a valid
signature and with the routed model the trace names; and that one changed byte breaks the signature. Owner:
integrator.
"""
from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import nightshift_ledger as ledger  # noqa: E402

EXCERPT = REPO_ROOT / "examples" / "live-trace-nightshift" / "nightshift-ledger" / "receipts.jsonl"
# RFC 8032 section 7.1, TEST 1 and TEST 2: (public key, message, signature).
RFC8032 = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]


ZETARIS_NIGHTSHIFT = REPO_ROOT / "examples" / "live-trace-zetaris-nightshift"


def cited_sources() -> list:
    traces = sorted({p.parent for p in (REPO_ROOT / "examples" / "live-trace-nightshift").rglob("run.jsonl")})
    traces += sorted({p.parent for p in ZETARIS_NIGHTSHIFT.rglob("run.jsonl")})
    examples = sorted(p for p in (REPO_ROOT / "examples").glob("0*-*") if (p / "run.jsonl").is_file())
    return traces + examples + [REPO_ROOT / "fixtures" / "transcripts",
                                REPO_ROOT / "examples" / "live-trace-nightshift" / "llama-3.2-3b" / "fixtures-transcripts"]


class Ed25519(unittest.TestCase):
    def test_rfc8032_vectors_verify_and_a_flipped_bit_does_not(self) -> None:
        for pub, msg, sig in RFC8032:
            pub_b, msg_b, sig_b = bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)
            self.assertTrue(ledger.ed25519_verify(pub_b, msg_b, sig_b))
            self.assertFalse(ledger.ed25519_verify(pub_b, msg_b + b"\x00", sig_b))
            self.assertFalse(ledger.ed25519_verify(pub_b, msg_b, sig_b[:-1] + bytes([sig_b[-1] ^ 1])))


class CommittedLedgerExcerpt(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lines = EXCERPT.read_text(encoding="utf-8").splitlines()
        cls.by_id = {json.loads(l)["body"]["receipt"]["receipt_id"]: json.loads(l) for l in cls.lines}

    def test_every_cited_receipt_is_in_the_ledger_signed_and_names_the_traced_model(self) -> None:
        requests = [req for source in cited_sources() for req in ledger.requests_of(source)]
        self.assertGreaterEqual(len({r["receipt_id"] for r in requests}), 157)
        for req in requests:
            envelope = self.by_id.get(req["receipt_id"])
            self.assertIsNotNone(envelope, req)
            self.assertTrue(ledger.verify_envelope(envelope), req["receipt_id"])
            line = ledger.ledger_line(req, envelope, True)
            self.assertTrue(line["run"]["same_model"], req)
            self.assertEqual(line["app"], "x402-adoption-investigator")
        self.assertEqual(len(self.lines), len(self.by_id), "one line per receipt")
        self.assertEqual(set(self.by_id), {r["receipt_id"] for r in requests}, "no receipt the repository does not cite")

    def test_the_video_run_is_answered_by_the_hosted_nemotron_in_the_ledger(self) -> None:
        run = REPO_ROOT / "examples" / "live-trace-nightshift" / "nemotron-cloud" / "live-video-ex01-20261024"
        lines = [ledger.ledger_line(r, self.by_id[r["receipt_id"]], True) for r in ledger.run_requests(run)]
        self.assertEqual([l["run"]["prompt_id"] for l in lines], ["planner", "writer-r1", "critic-r1", "writer-r2", "critic-r2"])
        self.assertEqual({(l["winner"], l["kind"], l["provider"], l["egress"]) for l in lines},
                         {("nvidia/nemotron-3.5-lightning-30b-a3b", "cloud", "nvidia", "nvidia")})

    def test_the_zetaris_runs_are_answered_by_the_routes_their_logs_name(self) -> None:
        """The six runs on Zetaris: 25 requests to the hosted Nemotron (egress nvidia), 5 to the local Nano (no egress),
        issued 22:36-22:42 UTC, and appended after the 127 lines the excerpt held before them."""
        routes = {}
        for run in sorted({p.parent for p in ZETARIS_NIGHTSHIFT.rglob("run.jsonl")}):
            for req in ledger.run_requests(run):
                line = ledger.ledger_line(req, self.by_id[req["receipt_id"]], True)
                self.assertTrue(line["run"]["same_model"], req)
                self.assertTrue("2026-10-24T22:36:00" <= line["issued_at"] <= "2026-10-24T22:43:00", line["issued_at"])
                key = (line["winner"], line["kind"], line["egress"])
                routes[key] = routes.get(key, 0) + 1
        self.assertEqual(routes, {("nvidia/nemotron-3.5-lightning-30b-a3b", "cloud", "nvidia"): 25,
                                  ("local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m", "local", "none"): 5})
        ids = [json.loads(l)["body"]["receipt"]["receipt_id"] for l in self.lines[127:]]
        self.assertEqual(len(ids), 30)
        self.assertEqual(set(ids), {r["receipt_id"] for run in ZETARIS_NIGHTSHIFT.rglob("run.jsonl")
                                    for r in ledger.run_requests(run.parent)})

    def test_one_changed_byte_breaks_the_signature(self) -> None:
        envelope = json.loads(self.lines[0])
        self.assertTrue(ledger.verify_envelope(envelope))
        envelope["body"]["receipt"]["winning_route"]["model"] = "some/other-model"
        self.assertFalse(ledger.verify_envelope(envelope))
        envelope = json.loads(self.lines[0])
        envelope["pubkey_b64"] = base64.b64encode(bytes(32)).decode()
        self.assertFalse(ledger.verify_envelope(envelope))


if __name__ == "__main__":
    unittest.main()
