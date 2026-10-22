"""metadata.json (the submission manifest: track, sponsor tech, tools, pre-existing components) stays true of the tree.

- every repository path it names exists;
- the test count it states is the number of tests unittest discovers in tests/ (this module included);
- README.md and the deck source quote that same count, so the pitch cannot drift from the suite.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
META = REPO_ROOT / "metadata.json"
TOP_DIRS = (".cursor", "docs", "examples", "fixtures", "investigator", "memory", "scripts", "tests", "viewer")
TOP_FILES = ("run.py", "README.md", "SPEC.md", "LICENSE", "Dockerfile", "docker-compose.yml", ".env.example",
             "INTEGRATION_NOTES.md", "pyproject.toml")
PATH_RE = re.compile(r"^(?:(?:" + "|".join(re.escape(d) for d in TOP_DIRS) + r")/[A-Za-z0-9_./-]*|"
                     + "|".join(re.escape(f) for f in TOP_FILES) + r")$")
QUOTED_COUNT_RE = re.compile(r"\b(\d{3}) tests\b")


def _strings(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)
    elif isinstance(node, str):
        yield node


class MetadataManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.meta = json.loads(META.read_text(encoding="utf-8"))

    def test_every_named_path_exists(self) -> None:
        paths = sorted({s for s in _strings(self.meta) if PATH_RE.match(s)})
        self.assertGreater(len(paths), 30, "metadata.json should point at the files it describes")
        missing = [p for p in paths if not (REPO_ROOT / p).exists()]
        self.assertEqual(missing, [], "metadata.json names paths that are not in the repository")

    def test_stated_test_count_is_the_discovered_count(self) -> None:
        suite = unittest.TestLoader().discover(str(TESTS_DIR), top_level_dir=str(TESTS_DIR))
        self.assertEqual(self.meta["tests"]["count"], suite.countTestCases(),
                         "update tests.count in metadata.json, README.md and docs/pitch/deck.html")

    def test_readme_and_deck_quote_the_same_count(self) -> None:
        count = self.meta["tests"]["count"]
        for rel in ("README.md", "docs/pitch/deck.html"):
            quoted = {int(n) for n in QUOTED_COUNT_RE.findall((REPO_ROOT / rel).read_text(encoding="utf-8"))}
            self.assertEqual(quoted, {count}, f"{rel} quotes a test count other than metadata.json's")


if __name__ == "__main__":
    unittest.main()
