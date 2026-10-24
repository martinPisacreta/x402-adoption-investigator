"""docs/DEMO_SCRIPT.md quotes seq numbers only through the table scripts/demo_seqs.py writes from the committed logs.
Owner: integrator."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import demo_seqs  # noqa: E402


class DemoScriptTable(unittest.TestCase):
    def test_the_table_is_what_the_logs_say(self) -> None:
        text = demo_seqs.DEMO_SCRIPT.read_text(encoding="utf-8")
        start = text.index(demo_seqs.BEGIN) + len(demo_seqs.BEGIN)
        table = text[start:text.index(demo_seqs.END)].strip()
        self.assertEqual(table, demo_seqs.render(), "run python scripts/demo_seqs.py --write")


if __name__ == "__main__":
    unittest.main()
