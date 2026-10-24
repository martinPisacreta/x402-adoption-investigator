"""The model second opinion's issues on sentences rule B fully backs are dismissed, logged and never acted on
(investigator/agents/critic.py, sentences_rule_b_backs). Owner: integrator.

A unit case over hand-built claims, then the committed examples: every `critique{rule: LLM, verdict: dismissed}` line
targets a sentence whose numbers the same round's narrative_map maps, with no rule-B issue on it, and the dismissed
reason never reaches the next Writer prompt (fixtures/transcripts/<example>/writer-r2-*.prompt.txt).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.agents import critic  # noqa: E402
from investigator.agents.writer import Answer  # noqa: E402
from investigator.contracts import Claim  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain",
            "04-payer-dossier-unverifiable", "05-payer-dossier-retry")


class StubLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, agent: str, payload: dict) -> int:
        self.events.append((kind, payload))
        return len(self.events)


class StubCtx:
    def __init__(self) -> None:
        self.log = StubLog()


class BackedSentences(unittest.TestCase):
    def test_only_sentences_whose_numbers_belong_to_their_cited_claims(self) -> None:
        claims = [Claim("c01", "sales_total", "s", "Sales total.", 41, "count", status="accepted"),
                  Claim("c02", "sales_own", "s", "Own sales.", 29, "count", status="accepted"),
                  Claim("c03", "gone", "s", "Rejected.", 7, "count", status="rejected")]
        answer = Answer(verdict="v", sentences=[
            "The receipts list 41 settled sales [c01].",          # backed: 41 is c01's value, c01 is cited
            "Of those, 29 were own-wallet sales [c02] out of 41.",  # 41 is not a value of c02 (the cited claim)
            "There were 13 services [c01].",                       # 13 maps to no claim: rule B flags it
            "Adoption looks real [c01].",                          # no number: nothing for rule B to back
            "Rejected count 7 [c03].",                             # cites a rejected claim
        ])
        narrative_map, b_issues = critic.narrative_pass_b(StubCtx(), answer, claims, [], 1)
        backed = critic.sentences_rule_b_backs(answer, claims, [], narrative_map, b_issues)
        self.assertEqual(backed, {0: ["c01"]})


class CommittedDismissals(unittest.TestCase):
    def test_every_dismissal_targets_a_sentence_rule_b_mapped(self) -> None:
        seen = 0
        for example in EXAMPLES:
            events = [json.loads(line) for line in (REPO_ROOT / "examples" / example / "run.jsonl").read_text(
                encoding="utf-8").splitlines()]
            maps = {e["payload"]["round"]: e["payload"] for e in events if e["type"] == "narrative_map"}
            b_targets = {(e["payload"]["round"], e["payload"].get("target")) for e in events
                         if e["type"] == "critique" and e["payload"].get("rule") == "B"
                         and e["payload"].get("verdict") == "revise"}
            prompts = " ".join(p.read_text(encoding="utf-8") for p in
                               (REPO_ROOT / "fixtures" / "transcripts" / example).glob("writer-r2-*.prompt.txt"))
            for e in events:
                p = e["payload"]
                if e["type"] != "critique" or p.get("rule") != "LLM" or p.get("verdict") != "dismissed":
                    continue
                seen += 1
                with self.subTest(example=example, seq=e["seq"]):
                    self.assertEqual(p["dismissed_by"], "B")
                    self.assertTrue(p["claim_ids"])
                    self.assertNotIn((p["round"], p["target"]), b_targets)
                    tokens = [t for t in maps[p["round"]]["tokens"] if t["sentence_index"] == p["target"]]
                    self.assertTrue([t for t in tokens if t["kind"] != "date"])
                    self.assertTrue(all(t["kind"] is not None for t in tokens))
                    if p["round"] == 1:
                        self.assertNotIn(p["reason"], prompts)   # a dismissed issue is not handed to the Writer
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
