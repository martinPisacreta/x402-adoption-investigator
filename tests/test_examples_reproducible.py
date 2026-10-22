"""Sample-mode run of the 4 examples == committed examples/*/output.json byte for byte (SPEC.md I4, 11). Owner: B4.

Skipped until scripts/regen_examples.py has produced the committed outputs. A fresh run writes to a temporary logs
directory; output.json carries no wall-clock values and `report_path` is the fixed `logs/<run_id>/report.md`, so
the bytes must match exactly.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain", "04-payer-dossier-unverifiable")


def committed(name: str) -> Path:
    return REPO_ROOT / "examples" / name / "output.json"


class ExamplesReproducible(unittest.TestCase):
    def test_example_inputs_exist(self) -> None:
        for name in EXAMPLES:
            self.assertTrue((REPO_ROOT / "examples" / name / "input.json").is_file(), name)

    @unittest.skipUnless(all(committed(n).is_file() for n in EXAMPLES),
                         "no committed output.json yet (run scripts/regen_examples.py once every agent is real)")
    def test_outputs_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            for name in EXAMPLES:
                payload = json.loads((REPO_ROOT / "examples" / name / "input.json").read_text(encoding="utf-8"))
                envelope = run_investigation(payload, config, echo_stdout=False)
                self.assertIn(envelope["status"], ("ok", "partial"), envelope.get("error"))
                fresh = (Path(tmp) / envelope["run_id"] / "output.json").read_bytes()
                expected = committed(name).read_bytes()
                if fresh != expected:
                    fresh_obj = json.loads(fresh.decode("utf-8"))
                    expected_obj = json.loads(expected.decode("utf-8"))
                    diff = [k for k in expected_obj.get("output", {}) if fresh_obj.get("output", {}).get(k) != expected_obj["output"].get(k)]
                    self.fail(f"{name}: output.json differs from the committed example (keys: {diff}); "
                              "regenerate with scripts/regen_examples.py and explain the change in docs/BUILD_LOG.md")
                # No committed file carries a machine path: handoff hashes strip export paths and the SQLite view
                # layer writes tables[].source_url as logs/<run_id>/exports/<file>, so every side file is compared.
                for side in ("run.jsonl", "sql.jsonl", "hmem.trace.jsonl", "report.md"):
                    if (REPO_ROOT / "examples" / name / side).is_file():
                        self.assertEqual((Path(tmp) / envelope["run_id"] / side).read_bytes(),
                                         (REPO_ROOT / "examples" / name / side).read_bytes(), f"{name}/{side}")


if __name__ == "__main__":
    unittest.main()
