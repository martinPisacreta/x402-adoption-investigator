"""Env files and variable aliases (investigator/config.py). Owner: integrator.

load_env_files() reads .env.local and then .env: the shell wins over both and .env.local over .env, and only names
are returned. ZETARIS_REST_URL, the name the hackathon guides suggest, is an alias of ZETARIS_BASE_URL.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import config  # noqa: E402


class EnvFiles(unittest.TestCase):
    def test_env_local_wins_over_env_and_the_shell_over_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"X402T_SHELL": "shell"}, clear=False):
            root = Path(tmp)
            (root / ".env").write_text("X402T_A=from-env\nX402T_B=from-env\nX402T_SHELL=from-env\n", encoding="utf-8")
            (root / ".env.local").write_text("X402T_B=from-local\n", encoding="utf-8")
            names = config.load_env_files(root)
            self.assertEqual(sorted(names), ["X402T_A", "X402T_B"])
            self.assertEqual((os.environ["X402T_A"], os.environ["X402T_B"], os.environ["X402T_SHELL"]),
                             ("from-env", "from-local", "shell"))
            for name in ("X402T_A", "X402T_B"):
                os.environ.pop(name, None)

    def test_env_alone_is_read_when_env_local_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            root = Path(tmp)
            (root / ".env").write_text("# comment\nX402T_ONLY=1\n", encoding="utf-8")
            self.assertEqual(config.load_env_files(root), ["X402T_ONLY"])
            os.environ.pop("X402T_ONLY", None)

    def test_an_inline_comment_after_an_unquoted_value_is_not_part_of_it(self) -> None:
        """.env.example's commented lines, once uncommented, must not carry their comment into the value."""
        names = ("X402T_STYLE", "X402T_KEY", "X402T_QUOTED", "X402T_HASH", "X402T_PORT")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            root = Path(tmp)
            (root / ".env.local").write_text(
                "X402T_STYLE=proxy     # proxy | direct\n"
                "X402T_KEY=            # GUI-created API key\n"
                "X402T_QUOTED=\"a # b\"\n"
                "X402T_HASH=pa#ss\n"
                "X402T_PORT=0\t# 0 = random free port\n", encoding="utf-8")
            config.load_env_files(root)
            got = tuple(os.environ.get(n) for n in names)
            for name in names:
                os.environ.pop(name, None)
        self.assertEqual(got, ("proxy", "", "a # b", "pa#ss", "0"))

    def test_empty_env_files_keep_auto_mode_sample(self) -> None:
        env = config.read_env({})
        self.assertEqual(config.resolve_mode("auto", env), "sample")


class ZetarisAliases(unittest.TestCase):
    def test_rest_url_is_an_alias_and_base_url_wins(self) -> None:
        self.assertEqual(config.read_env({"ZETARIS_REST_URL": "http://host.docker.internal:3000/"}).zetaris_base_url,
                         "http://host.docker.internal:3000")
        both = {"ZETARIS_BASE_URL": "http://a:3000", "ZETARIS_REST_URL": "http://b:3000"}
        self.assertEqual(config.read_env(both).zetaris_base_url, "http://a:3000")
        self.assertEqual(config.read_env({}).zetaris_base_url, "http://localhost:3000")


if __name__ == "__main__":
    unittest.main()
